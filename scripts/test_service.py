"""Service 层单测：写库副作用逐一验证（TDD：先于 API 存在）。

运行：
  .venv/Scripts/python.exe scripts/test_service.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server.service import ChatService, FollowupService, ProposalService  # noqa: E402
from starmoyu import storage  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}" + (f" | {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ❌ {name}" + (f" | {detail}" if detail else ""))


def main() -> None:
    ps, fs, cs = ProposalService(), FollowupService(), ChatService()

    print("== ProposalService ==")
    pid = ps.create_draft("测试需求：美妆精华，预算15万，4位腰部达人", None, created_by="u-admin")
    row = ps.get(pid)
    check("1.1 创建草稿落库", row is not None and row["status"] == "draft", f"pid={pid}")

    ps.update_content(pid, "## 一、需求理解\n(测试正文 v1)", changed_by="u-admin", comment="初稿")
    row = ps.get(pid)
    check("1.2 写入正文", "需求理解" in row["content"])

    try:
        ps.transition(pid, "approve", comment="越级审批")
        check("1.3 状态机拦截(draft 不能直接 approve)", False)
    except ValueError as e:
        check("1.3 状态机拦截(draft 不能直接 approve)", True, str(e)[:40])

    ps.transition(pid, "submit", changed_by="u-admin")
    row = ps.get(pid)
    check("1.4 submit → pending_review", row["status"] == "pending_review")

    ps.transition(pid, "revise", comment="预算压到9万",
                  new_content="## 修改版\n(测试正文 v2)", changed_by="u-admin")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*), max(version_no) FROM proposal_versions WHERE proposal_id=%s",
                    (pid,))
        n, vmax = cur.fetchone()
    check("1.5 revise 产生版本", n == 2 and vmax == 2, f"versions={n}")
    row = ps.get(pid)
    check("1.6 revise 后回到 pending_review", row["status"] == "pending_review")

    ps.transition(pid, "approve", changed_by="u-admin", comment="通过")
    row = ps.get(pid)
    check("1.7 approve → approved", row["status"] == "approved")

    print("\n== FollowupService ==")
    try:
        fs.create("DC-NOT-EXIST", "测试")
        check("2.1 不存在的商单被拒", False)
    except ValueError:
        check("2.1 不存在的商单被拒", True)

    fid = fs.create("DC20250026", "自动化测试跟进：电话联系达人确认进度", action_type="电话",
                    operator_id="u-admin")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT note, action_type, operator_id FROM deal_followup WHERE id=%s""", (fid,))
        r = cur.fetchone()
    check("2.2 跟进落库", r is not None and r[0].startswith("自动化测试"), f"fid={fid}")
    check("2.3 action_type 落库", r and r[1] == "电话")
    check("2.4 operator_id 落库", r and r[2] == "u-admin")

    print("\n== ChatService ==")
    conv = "conv-test-001"
    cs.ensure_conversation(conv, "u-admin", "测试会话")
    cs.ensure_conversation(conv, "u-admin")  # 幂等
    m1 = cs.add_message(conv, "user", "帮我看看美妆腰部达人")
    m2 = cs.add_message(conv, "assistant", "找到了 5 位",
                        tool_calls={"name": "search_kols", "args": {"category": "美妆"}})
    h = cs.history(conv)
    check("3.1 会话创建幂等", True)
    check("3.2 消息写入", len(h) == 2, f"n={len(h)}")
    check("3.3 tool_calls JSONB 存取", h[1]["tool_calls"]["name"] == "search_kols")

    print("\n" + "=" * 50)
    print(f"结果: {passed}/{passed + failed} 通过")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
