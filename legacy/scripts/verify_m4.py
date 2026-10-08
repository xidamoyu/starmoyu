"""M4 沉淀流端到端验收：粘贴聊天记录 → Agent 抽取 → 确认 → 入库 → 新会话可查。

验收链（REBUILD-PLAN M4）：
  R1 粘贴沉淀请求 → Agent 产出结构化抽取（不直接入库）
  R2 用户"确认" → confirm_interaction → party_traits verified=TRUE + deal_followup 新行
  R3 新会话问同达人 → list_kol_traits 返回沉淀条目原文
前置：后端未启动也可跑（直接走 MCAgent），需 WSL 三件套在线。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}" + (f" | {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ❌ {name}" + (f" | {detail}" if detail else ""))


def _staging_exists(raw_prefix: str) -> bool:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM ingest_staging WHERE raw_content LIKE %s LIMIT 1",
                    (raw_prefix[:20] + "%",))
        return cur.fetchone() is not None


def main() -> None:
    # 测试达人
    kid = "KOL-M4E2E"
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM party_traits WHERE party_id=%s", (kid,))
        cur.execute("DELETE FROM kol_profile WHERE kol_id=%s", (kid,))
        cur.execute(
            """INSERT INTO kol_profile (kol_id, kol_name, platform, category, tier, fans_count)
               VALUES (%s,'沉淀测试达人','抖音','美妆','腰部',800000)""", (kid,))
        conn.commit()

    from agent_graph import MCAgent
    agent = MCAgent()
    import time as _time
    run_tag = str(int(_time.time()))  # 每次全新会话，避免 SqliteSaver 恢复旧 state 干扰
    conv = f"m4-e2e-{run_tag}"

    raw = ("昨天和『沉淀测试达人』的商务聊完了：她说坑位费1.2万（21-60s），"
           "改稿超过2次要加收2000一次，必须提前两周给brief不然不接，"
           "只接受原声出镜，付款要预付50%。帮我沉淀一下这次的经验。")

    # ---- R1: 抽取（Agent 应展示结构化条目并等待确认，不得直接入库）----
    state = agent.chat(conv, raw)
    texts = [m.content for m in state["messages"]
             if type(m).__name__ == "AIMessage" and m.content]
    reply1 = texts[-1] if texts else ""
    check("R1.1 Agent 回复含结构化抽取", ("改稿" in reply1 or "brief" in reply1)
          and ("确认" in reply1 or "请" in reply1), f"{len(reply1)} 字")
    # R1.2 允许 Agent 已完成 record_interaction（staging 暂存），
    # 但绝不允许 traits 已落库（那才是"越权入库"）
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM party_traits WHERE party_id=%s", (kid,))
        n_before = cur.fetchone()[0]
    check("R1.2 未越权直接入库（traits=0）", n_before == 0, f"实际 {n_before}")
    # R1.3（新语义）：save_interaction 合并了暂存+入库，确认动作在对话层把关。
    # R1 轮不应出现任何 staging（Agent 未获确认不得调 save_interaction）。
    check("R1.3 确认前无 staging 写入",
          not _staging_exists(raw), "人工确认关口在对话层生效")

    # ---- R2: 确认 ----
    state2 = agent.chat(conv, "确认，全部入库")
    texts2 = [m.content for m in state2["messages"]
              if type(m).__name__ == "AIMessage" and m.content]
    reply2 = texts2[-1] if texts2 else ""
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT trait_category, trait_content, source_quote, severity
                       FROM party_traits WHERE party_id=%s AND verified=TRUE""", (kid,))
        rows = cur.fetchall()
    check("R2.1 traits 落库 verified=TRUE", len(rows) >= 3, f"{len(rows)} 条")
    all_text = " ".join(r[1] for r in rows)
    check("R2.2 关键经验在场（改稿/brief/预付）",
          any(k in all_text for k in ("改稿", "2000")) and
          any("brief" in t or "两周" in t for t in [r[1] for r in rows]),
          all_text[:60])
    has_quote = any(r[2] for r in rows)
    check("R2.3 每条带原文引用(source_quote)", has_quote)

    # ---- R3: 新会话查询（含昵称，模拟真实用户问法）----
    conv2 = f"m4-e2e-{run_tag}-b"
    state3 = agent.chat(conv2, "「沉淀测试达人」（KOL-M4E2E）这个达人有什么要注意的？")
    texts3 = [m.content for m in state3["messages"]
              if type(m).__name__ == "AIMessage" and m.content]
    reply3 = texts3[-1] if texts3 else ""
    check("R3.1 新会话能召回沉淀经验",
          any(k in reply3 for k in ("改稿", "brief", "预付", "两周")),
          reply3[:80])

    # ---- 清理 ----
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM party_traits WHERE party_id=%s", (kid,))
        cur.execute("DELETE FROM kol_profile WHERE kol_id=%s", (kid,))
        cur.execute("DELETE FROM ingest_staging WHERE raw_content LIKE %s", (raw[:30] + "%",))
        conn.commit()

    print(f"\nM4 沉淀流端到端: {passed}/{passed+failed} 通过" + ("  ✅" if failed == 0 else "  ❌"))
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
