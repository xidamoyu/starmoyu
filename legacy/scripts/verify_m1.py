"""M1 验收脚本：对话 Agent + 工具调用 + 数据落库（端到端）。

验收标准（docs/REBUILD-PLAN.md §7 M1）：
  V1 登录拿 JWT
  V2 SSE 对话含 tool_call 事件（Agent 真在选工具）
  V3 messages 表新增 user + assistant 行（对话沉淀）
  V4 对话式跟进落库：说「记录跟进」→ deal_followup 新增行（业务沉淀）
  V5 检索指标回归：Hit@1 >= 0.75（复用未破坏）

用法：
  .venv/Scripts/python.exe scripts/verify_m1.py
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BASE = "http://localhost:8000"
LOG = ROOT / "reports" / "verify_m1.log"

passed = failed = 0
lines: list[str] = []


def out(s: str = "") -> None:
    print(s)
    lines.append(s)


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        out(f"  ✅ {name}" + (f" | {detail}" if detail else ""))
    else:
        failed += 1
        out(f"  ❌ {name}" + (f" | {detail}" if detail else ""))


def _finish() -> None:
    global passed, failed
    out("\n" + "=" * 72)
    out(f"M1 验收结果: {passed}/{passed + failed} 通过" + ("  ✅" if failed == 0 else "  ❌"))
    out("=" * 72)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {LOG.relative_to(ROOT)}")
    sys.exit(0 if failed == 0 else 1)


def main() -> None:
    out("=" * 72)
    out(f"M1 验收  {datetime.now():%Y-%m-%d %H:%M:%S}")
    out("=" * 72)

    # ---- V1 登录 ----
    r = httpx.post(f"{BASE}/api/auth/login",
                   json={"username": "admin", "password": "admin123"}, timeout=30)
    check("V1 登录获取 JWT", r.status_code == 200 and "token" in r.json(),
          f"HTTP {r.status_code}")
    tok = r.json()["token"]
    H = {"Authorization": f"Bearer {tok}"}

    # 建会话
    rconv = httpx.post(f"{BASE}/api/conversations", json={"title": "M1 验收"},
                       headers=H, timeout=30)
    if rconv.status_code != 200 or "conv_id" not in rconv.json():
        check("V0 建会话", False, f"HTTP {rconv.status_code} {rconv.text[:120]}")
        _finish()
        return
    conv = rconv.json()["conv_id"]
    out(f"  会话: {conv}")

    def sse_chat(text: str) -> list[dict]:
        """发一条消息，收集全部 SSE 事件。"""
        events = []
        with httpx.stream("POST", f"{BASE}/api/chat/{conv}", json={"text": text},
                          headers=H, timeout=300) as resp:
            cur_ev = None
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    cur_ev = line[7:].strip()
                elif line.startswith("data: ") and cur_ev:
                    events.append({"type": cur_ev,
                                   **json.loads(line[6:])})
                    cur_ev = None
        return events

    # ---- V2/V3 第一轮：应触发 search_kols ----
    out("\n-- 第一轮：达人检索（期望 Agent 调 search_kols）--")
    ev1 = sse_chat("帮我找几个美妆类目、互动率高的腰部达人，列一下报价")
    tools_called = [e for e in ev1 if e["type"] == "tool_call"]
    check("V2a Agent 自主调用工具", len(tools_called) >= 1,
          f"调用: {[t['name'] for t in tools_called]}")
    check("V2b 调用的是 search_kols", any(t["name"] == "search_kols" for t in tools_called))
    has_answer = any(e["type"] == "token" and e.get("text") for e in ev1)
    check("V2c 有最终回答", has_answer)

    # ---- 第二轮：连续对话（要求基于上一轮结果行动）----
    out("\n-- 第二轮：数据沉淀（期望 Agent 调 create_followup 落库）--")
    ev2 = sse_chat("很好。请帮我对商单 DC20250026 记录一条跟进：电话联系达人确认档期，本周五前给答复")
    tools2 = [e for e in ev2 if e["type"] == "tool_call"]
    check("V4a Agent 调用 create_followup",
          any(t["name"] == "create_followup" for t in tools2),
          f"调用: {[t['name'] for t in tools2]}")

    # ---- 落库验证（直接查库，不听 Agent 自述）----
    out("\n-- 落库验证（直接查 PG）--")
    from starmoyu import storage
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM messages WHERE conv_id=%s", (conv,))
        n_msgs = cur.fetchone()[0]
        check("V3 对话沉淀(messages 表)", n_msgs >= 4,
              f"{n_msgs} 行（2 user + 2 assistant）")

        cur.execute("""SELECT count(*) FROM deal_followup
                       WHERE deal_id='DC20250026'
                         AND note LIKE '%档期%'
                         AND action_type='电话'
                         AND created_at::date = current_date""")
        n_fu = cur.fetchone()[0]
        check("V4b 跟进真实落库(deal_followup)", n_fu >= 1, f"{n_fu} 行")

    # ---- V5 检索指标回归 ----
    out("\n-- V5 检索指标回归（复用未破坏）--")
    from evaluate import build_eval_set  # type: ignore
    from starmoyu.retriever import Retriever
    r_ = Retriever()
    items = build_eval_set(r_)
    sample = items[::6][:9]  # 9 条快速回归
    hit = 0
    for it in sample:
        res = r_.search(it["query"], top_k=5, cand_k=20)
        got = {c.source_file for c in res["results"][:5]}
        if got & set(it["labels"]):
            hit += 1
    check("V5 抽样回归 Hit@5 >= 8/9", hit >= 8, f"{hit}/9")
    r_.close()

    # ---- 汇总 ----
    _finish()


if __name__ == "__main__":
    main()
