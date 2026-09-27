"""M5 端到端验收：走真实 Agent 循环验证五条新流。

R1 主动简报：开场打招呼 → 必调 get_today_briefing
R2 结案复盘：贴效果数据 → 确认卡（不入库）→ 确认 → deal.result_metrics 更新 + stage=结案
R3 复盘召回：问效果 → get_deal_result 返回原值
R4 Brief 接单：贴甲方需求 → 确认卡 → 确认 → proposals 草稿 + 组合建议
R5 数据飞轮：match_kols_for_requirement 返回 hist_deals/avg_roi（确定性，不依赖 LLM）

运行（后台）：.venv/Scripts/python.exe scripts/verify_m5.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from starmoyu import storage  # noqa: E402
from agent_graph import MCAgent  # noqa: E402

RUN_TAG = str(int(time.time()))
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{name}: {detail}")
    print(("✅" if ok else "❌"), name, detail)


def agent_reply(agent: MCAgent, thread: str, text: str) -> tuple[str, list[str]]:
    st = agent.chat(thread, text)
    tools, final = [], ""
    for m in st["messages"]:
        if hasattr(m, "tool_calls") and m.tool_calls:
            tools += [tc["name"] for tc in m.tool_calls]
        if m.__class__.__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
            final = m.content or ""
    return final, tools


def pick_exec_deal() -> str:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT deal_id FROM deal WHERE stage='执行' AND brand_name ILIKE '%测试%' LIMIT 1")
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("SELECT deal_id FROM deal WHERE stage='执行' ORDER BY deal_id LIMIT 1")
        return cur.fetchone()[0]


def snapshot_deal(deal_id: str) -> dict:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT result_metrics, stage FROM deal WHERE deal_id=%s", (deal_id,))
        row = cur.fetchone()
        return {"metrics": row[0], "stage": row[1]}


def restore_deal(deal_id: str, snap: dict) -> None:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE deal SET result_metrics=%s, stage=%s WHERE deal_id=%s",
                    (snap["metrics"], snap["stage"], deal_id))
        cur.execute("DELETE FROM deal_followup WHERE deal_id=%s AND action_type='结案复盘'", (deal_id,))
        cur.execute("""DELETE FROM proposal_versions WHERE proposal_id IN (
                       SELECT proposal_id FROM proposals
                       WHERE created_at > now() - interval '1 hour'
                         AND requirement_text LIKE '%新锐美妆品牌%')""")
        cur.execute("""DELETE FROM proposals WHERE created_at > now() - interval '1 hour'
                       AND requirement_text LIKE '%新锐美妆品牌%'""")
        cur.execute("DELETE FROM ingest_staging WHERE created_by='agent' AND status='confirmed' "
                    "AND suggested_kind IN ('deal_result','brief')")
        conn.commit()


def main() -> int:
    print(f"=== M5 端到端验收 RUN {RUN_TAG} ===")
    agent = MCAgent()
    print("checkpointer:", agent.cp_kind)

    deal_id = pick_exec_deal()
    snap = snapshot_deal(deal_id)
    print(f"测试商单: {deal_id} (stage={snap['stage']})")

    thread_b = f"m5-briefing-{RUN_TAG}"
    reply, tools = agent_reply(agent, thread_b, "你好，今天有什么要处理的？")
    check("R1 主动简报调工具", "get_today_briefing" in tools, f"tools={tools}")
    check("R1 简报含事项数字", any(w in reply for w in ("待审批", "档期", "跟进", "黑名单", "无")),
          f"len={len(reply)}")

    thread_r = f"m5-result-{RUN_TAG}"
    paste = (f"DC测试结案数据：商单{deal_id}刚结案，这波投放ROI 2.3，GMV 18万，"
             f"曝光420万，互动量6.5万。帮我归档。")
    reply1, tools1 = agent_reply(agent, thread_r, paste)
    # 确认前：deal 必须未变
    cur_state = snapshot_deal(deal_id)
    metrics_json = json.dumps(cur_state["metrics"], ensure_ascii=False, default=str) if cur_state["metrics"] else ""
    check("R2 确认前不落库", "2.3" not in metrics_json, "deal.result_metrics 未含 2.3")
    has_card = ("2.3" in reply1) and ("确认" in reply1)
    check("R2 确认卡展示数字", has_card, f"tools={tools1}")

    reply2, tools2 = agent_reply(agent, thread_r, "确认")
    check("R2 确认后调 save_deal_result", "save_deal_result" in tools2, f"tools={tools2}")
    after = snapshot_deal(deal_id)
    m = after["metrics"] if isinstance(after["metrics"], dict) else {}
    check("R2 效果指标落库", m.get("roi") == 2.3 and m.get("gmv") == 180000, f"metrics={m}")
    check("R2 stage 置结案", after["stage"] == "结案", f"stage={after['stage']}")

    thread_q = f"m5-recall-{RUN_TAG}"
    reply3, tools3 = agent_reply(agent, thread_q, f"查一下商单{deal_id}的结案效果怎么样")
    check("R3 复盘召回原值", "2.3" in reply3 and ("get_deal_result" in tools3),
          f"tools={tools3}")

    thread_p = f"m5-brief-{RUN_TAG}"
    paste_b = ("测试brief：我们是一个新锐美妆品牌，想投3个腰部达人做新品推广，"
               "总预算9万，最好两周内发布，不能接纯口播。先给我看看解析结果，先别建方案。")
    reply4, tools4 = agent_reply(agent, thread_p, paste_b)
    # 确认卡判定：回复含预算数字与"确认"字样，且本轮未调任何写库工具
    wrote_early = [t for t in tools4 if t in ("save_brief", "create_proposal")]
    has_card4 = ("9万" in reply4 or "90000" in reply4 or "90,000" in reply4) and ("确认" in reply4)
    check("R4 brief 确认卡", has_card4 and not wrote_early,
          f"tools={tools4} card={has_card4}")
    reply5, tools5 = agent_reply(agent, thread_p, "确认，建方案吧")
    check("R4 确认后调 save_brief", "save_brief" in tools5, f"tools={tools5}")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT proposal_id, status, content FROM proposals
                       WHERE created_at > now() - interval '10 minutes'
                         AND requirement_text LIKE '%新锐美妆品牌%' ORDER BY created_at DESC LIMIT 1""")
        prow = cur.fetchone()
    check("R4 草稿落库", bool(prow) and prow[1] == "draft",
          f"proposal={prow[0] if prow else None}")
    check("R4 组合建议返回", ("组合" in reply5 or "达人" in reply5), f"len={len(reply5)}")
    if prow:
        content = prow[2] if isinstance(prow[2], str) else json.dumps(prow[2], ensure_ascii=False)
        check("R4 需求卡内容", "美妆" in content, content[:80])

    # R5 数据飞轮（确定性断言，不依赖 LLM）
    from agent_tools import match_kols_for_requirement
    r = json.loads(match_kols_for_requirement.invoke(
        {"category": "美妆", "budget": 100000, "kol_count": 3}))
    combo = r.get("combo", [])
    has_hist = any(c.get("hist_deals", 0) > 0 and c.get("avg_roi") is not None for c in combo)
    check("R5 匹配器带历史效果", bool(combo) and has_hist,
          json.dumps([{k: c.get(k) for k in ("kol_id", "hist_deals", "avg_roi")}
                      for c in combo], ensure_ascii=False))

    restore_deal(deal_id, snap)
    restored = snapshot_deal(deal_id)
    check("清理恢复", restored["stage"] == snap["stage"], "测试数据已还原")

    print(f"\n=== 结果 {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
    for f in FAIL:
        print("  FAIL:", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
