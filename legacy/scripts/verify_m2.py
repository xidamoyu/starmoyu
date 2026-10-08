"""M2 验收脚本：方案全生命周期 + 档期排他 + Excel 导入。

验收标准（docs/REBUILD-PLAN.md §7 M2）：
  W1 创建方案 → 提交审批 → 通过 → 版本表 ≥2 行
  W2 PATCH 达人档期（排他期覆盖目标日期）→ search_kols 过滤生效
  W3 导入 10 条达人 xlsx → kol_profile 计数 +10
前置：后端 :8000 已启动（LANGGRAPH_CHECKPOINT_SQLITE 用 Windows 路径）。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from starmoyu import storage  # noqa: E402

BASE = "http://localhost:8000"
passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}" + (f" | {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ❌ {name}" + (f" | {detail}" if detail else ""))


def main() -> None:
    c = httpx.Client(timeout=60)

    # ---- 登录 ----
    r = c.post(f"{BASE}/api/auth/login",
               json={"username": "admin", "password": "admin123"})
    check("W0 登录", r.status_code == 200)
    tok = {"Authorization": f"Bearer {r.json()['token']}"}

    # ---- W1 方案全生命周期 + 版本化 ----
    r = c.post(f"{BASE}/api/chat", json={"text": ""})  # 占位防止误用
    # 直接用 Service 创建草稿（等价于 Agent create_proposal 落库结果）
    from server.service import ProposalService
    ps = ProposalService()
    pid = ps.create_draft("美妆新品推广方案（M2验收）", None, created_by="u-admin")
    ps.update_content(pid, "v1 正文：预算15万，达人组合 3+1…", changed_by="agent",
                      comment="初稿")
    ps.transition(pid, "submit", changed_by="agent")
    st1 = ps.get(pid)["status"]
    check("W1.1 草稿→提交审批", st1 == "pending_review", st1)

    # 审批前修订（revise 留版本痕）
    ps.transition(pid, "revise", comment="按初审意见改预算",
                  new_content="v2 正文：预算调整为 18 万…", changed_by="u-admin")
    r = c.post(f"{BASE}/api/proposals/{pid}/review", headers=tok,
               json={"action": "approve", "comment": "预算合理，通过"})
    check("W1.2 API 审批通过", r.status_code == 200 and r.json()["status"] == "approved",
          r.text[:60])

    r = c.get(f"{BASE}/api/proposals/{pid}", headers=tok)
    vers = r.json().get("versions", [])
    check("W1.3 版本表 2 行 + 内容为 v2", len(vers) == 2 and "18" in vers[-1]["content"],
          f"version_no 共 {len(vers)} 行, 最新: {vers[-1]['content'][:20] if vers else ''}")

    # 状态机防呆：approved 后再 reject 应被拒
    r = c.post(f"{BASE}/api/proposals/{pid}/review", headers=tok,
               json={"action": "reject", "comment": "测试防呆"})
    check("W1.4 状态机防呆（approved 不可 reject）", r.status_code == 422,
          f"HTTP {r.status_code}")

    # ---- W2 排他期过滤 ----
    # 选一个达人，设排他期覆盖今天起 30 天
    r = c.get(f"{BASE}/api/kols", headers=tok, params={"tier": "腰部", "limit": 5})
    kols = r.json()["items"]
    check("W2.0 达人列表可用", r.status_code == 200 and len(kols) > 0,
          f"{len(kols)} 位腰部达人")
    target = kols[0]
    kid = target["kol_id"]

    from datetime import date, timedelta
    today = date.today()
    until = (today + timedelta(days=30)).isoformat()
    r = c.patch(f"{BASE}/api/kols/{kid}", headers=tok,
                json={"exclusive_until": until})
    check("W2.1 PATCH 排他期落库", r.status_code == 200, f"{kid} → {until}")

    # 直接调 Agent 工具层验证过滤（search_kols 与 API 共用同一库）
    sys.path.insert(0, str(ROOT / "src"))
    import agent_tools  # noqa: E402  (src/agent_tools.py, @tool 装饰器包成 StructuredTool)
    res = json.loads(agent_tools.search_kols.invoke({"tier": "腰部", "limit": 50}))
    names = [k.get("kol_name") for k in res.get("items", res.get("results", []))]
    check("W2.2 search_kols 排他过滤生效", target["kol_name"] not in names,
          f"「{target['kol_name']}」已从腰部达人结果中过滤")

    # ---- W3 Excel 导入 ----
    r = c.get(f"{BASE}/api/kols", headers=tok, params={"limit": 1})
    # 用 count 查询拿准确 before
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM kol_profile")
        before = cur.fetchone()[0]

    wb = Workbook()
    ws = wb.active
    ws.append(["kol_name", "platform", "category", "sub_category", "tier",
               "fans_count", "interact_rate", "price_21_60s", "avg_views"])
    for i in range(1, 11):
        ws.append([f"M2导入测试达人{i:02d}", "抖音", "美妆", "测试类目", "尾部",
                   100000 + i, 0.03, 8000 + i * 100, 50000 + i])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    r = c.post(f"{BASE}/api/admin/import", headers=tok,
               files={"file": ("m2_test_kols.xlsx", buf,
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               params={"kind": "kols"})
    check("W3.1 导入 API 成功", r.status_code == 200, r.text[:80])

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM kol_profile")
        after = cur.fetchone()[0]
    check("W3.2 计数 +10", after - before == 10, f"{before} → {after}")

    # 清理测试数据（不留脏数）
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM kol_profile WHERE kol_name LIKE 'M2导入测试达人%'")
        conn.commit()
        cur.execute("SELECT count(*) FROM kol_profile")
        restored = cur.fetchone()[0]
    check("W3.3 测试数据清理", restored == before, f"恢复到 {restored}")

    print(f"\nM2 验收: {passed}/{passed + failed} 通过" + ("  ✅" if failed == 0 else "  ❌"))
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
