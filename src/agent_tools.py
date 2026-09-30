"""Agent 工具层：LLM 通过 bind_tools 自主选择调用。

设计见 docs/REBUILD-PLAN.md §2.2。
- 只读工具：search_knowledge / search_kols / get_deal_status / match_kols_for_requirement
- 写库工具：create_proposal / update_proposal_status / create_followup
  （写库工具调用 service 层，service 是唯一写库入口 —— 数据沉淀的关键）
- 错误一律返回结构化字符串（LLM 可读可纠正），不抛异常
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402
from starmoyu.retriever import Retriever  # noqa: E402

# 模块级共享检索器（BM25 索引初始化较重，进程内复用）
_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


def _rows(cur) -> list[dict]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _j(o):
    """JSON 安全序列化。"""
    if isinstance(o, (date, datetime)):
        return o.isoformat()[:10] if isinstance(o, date) and not isinstance(o, datetime) else o.isoformat()
    raise TypeError(str(type(o)))


# ============================================================ 只读工具

@tool
def search_knowledge(query: str, doc_type: str = "", category: str = "") -> str:
    """检索公司私有知识库（刊例报价、平台规则、历史商单案例、方法论）。

    何时使用：用户询问报价/规则/流程/历史案例，或生成方案需要依据时。
    Args:
        query: 检索问题，用自然语言描述要找的信息
        doc_type: 可选过滤。rate_card=刊例, platform_policy=平台规则,
                  methodology=方法论, deal_case=历史商单案例, deal_tracking=在途跟踪单
        category: 可选类目过滤，如 美妆/3C数码/食品饮料
    """
    try:
        r = get_retriever()
        res = r.search(query, top_k=5, cand_k=20)
        out = []
        for c in res["results"]:
            out.append({
                "title": c.doc_title, "section": c.section, "type": c.doc_type,
                "source": c.source_file, "content": c.content[:400],
            })
        if not out:
            return json.dumps({"found": False,
                               "hint": "知识库中无相关内容。回答时应明确告知用户未找到依据，禁止编造。"},
                              ensure_ascii=False)
        return json.dumps({"found": True, "n": len(out), "hits": out}, ensure_ascii=False, default=_j)
    except Exception as e:
        return json.dumps({"error": f"检索失败 {type(e).__name__}: {e}"}, ensure_ascii=False)


@tool
def search_kols(category: str = "", tier: str = "", fans_min: int = 0,
                fans_max: int = 0, price_max: int = 0, target_date: str = "",
                name: str = "", limit: int = 8) -> str:
    """搜索达人库，可按名字/类目/量级/粉丝区间/报价上限筛选，并自动排除档期冲突。

    何时使用：用户问「有哪些达人」「找个美妆腰部达人」「谁在 XX 价格内」，
    以及「找XX这个达人的档期/报价/粉丝」（用 name 参数，不要换类目反复试）。
    Args:
        category: 类目，如 美妆/3C数码/食品饮料/母婴/服饰/家居/汽车/游戏
        tier: 量级：头部/腰部/尾部，留空为不限
        fans_min/fans_max: 粉丝数区间（整数）
        price_max: 21-60s 刊例报价上限（元）
        target_date: 目标投放日期 YYYY-MM-DD；提供时会排除排他期内/档期未到的达人
        name: 达人名字（模糊匹配，用户点名找某人时用它）
        limit: 返回条数上限，默认 8
    """
    try:
        conds, args = ["1=1"], []
        if name:
            conds.append("kol_name LIKE %s"); args.append(f"%{name}%")
        if category:
            conds.append("category = %s"); args.append(category)
        if tier:
            conds.append("tier = %s"); args.append(tier)
        if fans_min:
            conds.append("fans_count >= %s"); args.append(fans_min)
        if fans_max:
            conds.append("fans_count <= %s"); args.append(fans_max)
        if price_max:
            conds.append("price_21_60s <= %s"); args.append(price_max)
        sql = f"""SELECT kol_id, kol_name, category, sub_category, fans_count, tier,
                         avg_views, interact_rate, price_21_60s, cpm,
                         exclusive_until, available_from, blacklist
                  FROM kol_profile WHERE {' AND '.join(conds)}
                  ORDER BY interact_rate DESC NULLS LAST LIMIT %s"""
        args.append(int(limit))
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            rows = _rows(cur)

        conflicts = []
        if target_date:
            td = date.fromisoformat(target_date)
            kept = []
            for r0 in rows:
                eu, af, bl = r0.get("exclusive_until"), r0.get("available_from"), r0.get("blacklist")
                if bl:
                    conflicts.append({**r0, "conflict_reason": "黑名单"}); continue
                if eu and eu >= td:
                    conflicts.append({**r0, "conflict_reason": f"排他期至 {eu}"}); continue
                if af and af > td:
                    conflicts.append({**r0, "conflict_reason": f"最早可接 {af}"}); continue
                kept.append(r0)
            rows = kept

        slim = [{"kol_id": x["kol_id"], "name": x["kol_name"],
                 "category": f"{x['category']}/{x['sub_category']}",
                 "tier": x["tier"], "fans": x["fans_count"],
                 "interact": x["interact_rate"], "price": x["price_21_60s"],
                 "avg_views": x["avg_views"], "cpm": x.get("cpm")} for x in rows]
        return json.dumps({"n": len(slim), "kols": slim,
                           "excluded_by_conflict": len(conflicts),
                           "conflicts": [{"kol_id": c["kol_id"], "reason": c["conflict_reason"]}
                                          for c in conflicts[:5]]},
                          ensure_ascii=False, default=_j)
    except Exception as e:
        return json.dumps({"error": f"达人检索失败 {type(e).__name__}: {e}"}, ensure_ascii=False)


@tool
def get_deal_status(deal_id: str = "", brand_name: str = "") -> str:
    """查询商单状态：阶段、预算、停滞天数、风险等级。

    何时使用：用户问「某商单进展如何」「哪些商单卡住了」「XX 品牌的单子怎么样」。
    Args:
        deal_id: 商单编号，如 DC20250026（与 brand_name 二选一）
        brand_name: 广告主名称，模糊匹配
    """
    try:
        if not deal_id and not brand_name:
            # 概览：全部在途按风险分级
            today = date.today()
            with storage.pg_connect() as conn:
                cur = conn.cursor()
                cur.execute("""SELECT deal_id, brand_name, stage, budget, updated_at, end_date
                               FROM deal
                               WHERE stage IN ('需求沟通','提案','签约','执行')""")
                rows = _rows(cur)
            hi, mid = [], []
            for d in rows:
                sd = (today - d["updated_at"].date()).days
                overdue = today > d["end_date"]
                lvl = "高" if (sd >= 7 or overdue) else ("中" if sd >= 3 else None)
                item = {"deal_id": d["deal_id"], "brand": d["brand_name"],
                        "stage": d["stage"], "stagnant_days": sd, "overdue": overdue}
                (hi if lvl == "高" else mid).append(item) if lvl else None
            return json.dumps({"overview": True, "inflight_total": len(rows),
                               "high_risk": hi[:10], "high_count": len(hi),
                               "medium_count": len(mid)},
                              ensure_ascii=False, default=_j)
        conds, args = [], []
        if deal_id:
            conds.append("deal_id = %s"); args.append(deal_id)
        if brand_name:
            conds.append("brand_name ILIKE %s"); args.append(f"%{brand_name}%")
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(f"""SELECT d.*, (SELECT count(*) FROM deal_followup f
                             WHERE f.deal_id=d.deal_id) AS followups
                             FROM deal d{where} LIMIT 5""")
            rows = _rows(cur)
        today = date.today()
        for d in rows:
            d["stagnant_days"] = (today - d["updated_at"].date()).days
            d["overdue"] = today > d["end_date"]
            d["risk"] = ("高" if (d["stagnant_days"] >= 7 or d["overdue"])
                         else "中" if d["stagnant_days"] >= 3 else "正常")
        return json.dumps({"n": len(rows), "deals": rows}, ensure_ascii=False, default=_j)
    except Exception as e:
        return json.dumps({"error": f"商单查询失败 {type(e).__name__}: {e}"}, ensure_ascii=False)


@tool
def match_kols_for_requirement(category: str, budget: int, kol_count: int,
                               kol_tier: str = "") -> str:
    """按预算约束给出达人组合建议（自动均摊预算并检查单人报价）。

    何时使用：用户描述了明确需求（类目+预算+人数），要求组合建议。
    Args:
        category: 类目，如 美妆
        budget: 总预算（元）
        kol_count: 达人数量
        kol_tier: 量级偏好：头部/腰部/尾部，留空为不限
    """
    try:
        per = budget / max(kol_count, 1) * 0.85  # 保守均摊
        conds = ["category = %s", "price_21_60s <= %s"]
        args = [category, int(per)]
        if kol_tier and kol_tier != "不限":
            conds.append("tier = %s"); args.append(kol_tier)
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(f"""SELECT k.kol_id, k.kol_name, k.tier, k.fans_count, k.interact_rate,
                                   k.price_21_60s, k.exclusive_until,
                                   (SELECT count(*) FROM deal d
                                     WHERE d.stage='结案' AND d.result_metrics IS NOT NULL
                                       AND d.kol_ids @> to_jsonb(k.kol_id::text)) AS hist_deals,
                                   (SELECT avg((d.result_metrics->>'roi')::numeric) FROM deal d
                                     WHERE d.stage='结案' AND d.result_metrics->>'roi' IS NOT NULL
                                       AND d.kol_ids @> to_jsonb(k.kol_id::text)) AS avg_roi
                            FROM kol_profile k WHERE {' AND '.join(conds)}
                            ORDER BY (CASE WHEN k.tier='腰部' THEN 0 WHEN k.tier='尾部' THEN 1 ELSE 2 END),
                                     k.interact_rate DESC NULLS LAST
                            LIMIT 10""", args)
            rows = _rows(cur)
        if not rows:
            return json.dumps({"match": False,
                               "hint": f"该预算下（人均≈{per:,.0f}元）无匹配达人，可建议放宽量级或预算"},
                              ensure_ascii=False)
        combos, total = [], 0
        for r0 in rows[:kol_count]:
            combos.append(r0); total += r0["price_21_60s"]
        return json.dumps({"per_kol_budget": round(per),
                           "combo": [{"kol_id": c["kol_id"], "name": c["kol_name"],
                                      "tier": c["tier"], "fans": c["fans_count"],
                                      "price": c["price_21_60s"],
                                      "hist_deals": c.get("hist_deals") or 0,
                                      "avg_roi": (round(float(c["avg_roi"]), 2)
                                                  if c.get("avg_roi") is not None else None)}
                                     for c in combos],
                           "total_price": total,
                           "within_budget": total <= budget},
                          ensure_ascii=False, default=_j)
    except Exception as e:
        return json.dumps({"error": f"组合匹配失败 {type(e).__name__}: {e}"}, ensure_ascii=False)


# ============================================================ 写库工具

SERVICE = None  # 延迟导入，避免循环依赖


def _service():
    global SERVICE
    if SERVICE is None:
        from server.service import ProposalService, FollowupService
        SERVICE = {"proposal": ProposalService(), "followup": FollowupService()}
    return SERVICE


@tool
def create_proposal(requirement_text: str, deal_id: str = "") -> str:
    """创建商单构思方案（写入数据库，状态为草稿待审批）。

    何时使用：仅限【没有 brief 原文】、用户口头描述需求并明确要求「做个方案」的场景。
    【重要】如果用户粘贴了甲方 brief 原文（哪怕是转述），必须用 save_brief 而不是本工具——
    save_brief 会走暂存确认流程并留下需求卡版本痕，本工具不会。确认卡流程中禁止用本工具替代。
    注意：创建前应先用 match_kols_for_requirement 和 search_knowledge 拿到依据。
    Args:
        requirement_text: 完整需求描述
        deal_id: 关联商单编号（可选）
    """
    try:
        from server.service import ProposalService
        pid = ProposalService().create_draft(requirement_text, deal_id or None)
        return json.dumps({"ok": True, "proposal_id": pid,
                           "hint": "方案已存为草稿。生成完整方案正文后调用 update_proposal_status 提交审批，"
                                   "或由用户在审批中心处理。"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"创建方案失败 {type(e).__name__}: {e}"},
                          ensure_ascii=False)


@tool
def update_proposal_status(proposal_id: str, action: str, comment: str = "",
                           new_content: str = "") -> str:
    """更新方案状态：approve（通过）/ reject（驳回）/ revise（修改并版本化）。

    何时使用：用户对某个方案做出审批决定或要求修改。
    Args:
        proposal_id: 方案编号
        action: approve / reject / revise
        comment: 审批意见
        new_content: action=revise 时的新版本正文
    """
    try:
        from server.service import ProposalService
        ok = ProposalService().transition(proposal_id, action, comment, new_content or None)
        return json.dumps({"ok": ok, "proposal_id": proposal_id, "action": action},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"更新方案失败 {type(e).__name__}: {e}"},
                          ensure_ascii=False)


@tool
def create_followup(deal_id: str, note: str, action_type: str = "其他") -> str:
    """记录一条商单跟进动作（写入 deal_followup 表）。

    何时使用：用户说「记录跟进」「给这个单子加一条跟进」「我联系了达人」。
    Args:
        deal_id: 商单编号，如 DC20250026
        note: 跟进内容
        action_type: 电话/约见/催稿/结算/其他
    """
    try:
        from server.service import FollowupService
        fid = FollowupService().create(deal_id, note, action_type)
        return json.dumps({"ok": True, "followup_id": fid,
                           "hint": "跟进已落库。若本次跟进包含拒绝原因、返点、档期、"
                                   "改稿要求等关键信息，建议补充到 note 里（一句话即可）——"
                                   "结案复盘时这些要点会自动提炼进案例文档。"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"跟进落库失败 {type(e).__name__}: {e}"},
                          ensure_ascii=False)


@tool
def request_deal_change(deal_id: str, field_name: str, new_value: str,
                        change_summary: str = "", conv_id: str = "") -> str:
    """发起商单字段变更审批申请（写入 deal_change_requests，status=pending）。

    何时使用：用户在对话中告知商单内容变更（预算改 X 万、负责人换成 X 等），
    但我没有权限直接改 deal 主表 —— 用这个工具把变更申请送进审批流，
    由审批中心批准后才真正落 deal 表。一商单可挂多条变更。
    Args:
        deal_id: 商单编号，如 DC20260028
        field_name: 要改的字段，可选 budget/owner/stage/demand_desc/goal/cpm_target/platform_req
        new_value: 新值（budget/cpm_target 传数字，其余传文本）
        change_summary: 变更说明（用户原话，便于审批人理解）
        conv_id: 当前会话 ID（可选，溯源用）
    """
    try:
        from server.m7b_service import DealChangeService
        r = DealChangeService().create(deal_id, field_name, new_value,
                                       change_summary, conv_id, created_by="agent")
        return json.dumps({
            "ok": True, "request_id": r["request_id"], "status": r["status"],
            "field": r["field_name"], "old_value": r["old_value"], "new_value": r["new_value"],
            "hint": "变更申请已进审批流(pending)，审批中心批准后才真正生效。"},
            ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"变更申请失败 {type(e).__name__}: {e}"},
                          ensure_ascii=False)


@tool
def sediment_case(deal_id: str, extra_lessons: str = "", confirm: bool = False) -> str:
    """结案沉淀：把已结案商单渲染成案例文档回流 RAG 知识库（需用户确认后才入库）。

    何时使用：结案动作完成后（结案确认卡/表单返回的 hint 会提醒），
    **主动向用户提议沉淀**；或用户主动说「沉淀」「复盘一下」「存进知识库」。
    交互协议（严格按顺序）：
      1. 结案落地 → Agent 主动提醒用户"建议沉淀成本单案例"
      2. 用户同意沉淀 → 调本工具 confirm=false 出预览（草稿+提炼的复盘要点）
      3. 用户看过预览、同意入库 → 调本工具 confirm=true 增量入库
    未经用户两次同意（同意沉淀 + 同意预览内容），不得 confirm=true。
    Args:
        deal_id: 已结案的商单编号，如 DC20250005
        extra_lessons: 用户在预览时补充的经验要点，多条用分号分隔（可选）
        confirm: false=出预览草稿；true=用户确认预览内容后正式入库
    """
    try:
        from server.sediment_service import sediment_closed_deal, confirm_sediment
        lessons = [s.strip() for s in extra_lessons.split("；") if s.strip()] if extra_lessons else []
        if confirm:
            r = confirm_sediment(deal_id, lessons, confirmed_by="agent")
            ing = r["sediment"]
            return json.dumps({
                "ok": True, "deal_id": deal_id, "file": ing["doc_id"],
                "chunks": ing["chunks"], "replaced_old": ing["replaced_old"],
                "hint": f"案例已增量入库（{ing['chunks']} 块，替换旧 {ing['replaced_old']} 块），现在可以被检索了。"},
                ensure_ascii=False)
        r = sediment_closed_deal(deal_id, lessons)
        return json.dumps({
            "ok": True, "deal_id": deal_id, "title": r["title"],
            "char_len": r["char_len"], "preview": r["preview"],
            "hint": "以上是案例文档草稿。请向用户确认（复盘要点是否准确、是否补充经验），"
                    "确认后带 confirm=true 再次调用本工具正式入库。"},
            ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"沉淀失败 {type(e).__name__}: {e}"},
                          ensure_ascii=False)


@tool
def save_trait(party_type: str, party_id: str, trait_category: str,
               trait_content: str, source_quote: str = "") -> str:
    """沉淀合作画像（party_traits）：达人/品牌的合作经验，如档期习惯、沟通偏好、付款要求。

    何时使用：对话中出现对某达人/品牌的评价性信息（"这个达人档期很紧""该品牌总压价"），
    用户确认后调用本工具沉淀，未来合作决策时可直接引用。
    Args:
        party_type: "kol" 或 "brand"
        party_id: 达人ID（K开头）或品牌名
        trait_category: 沟通偏好/改稿态度/排期习惯/付款要求/内容尺度/其他
        trait_content: 画像内容（一句话）
        source_quote: 原话引用（溯源用，可选）
    """
    try:
        from server.m4_service import TraitService
        tid = TraitService().add(party_type, party_id, trait_category, trait_content,
                                 source_quote=source_quote, source_type="conversation",
                                 verified=False, created_by="agent")
        return json.dumps({
            "ok": True, "trait_id": tid, "verified": False,
            "hint": "画像已暂存（待确认）。请在对话中向用户展示内容，用户同意后"
                    "通过 POST /api/traits/{id}/confirm 或管理界面确认生效。"},
            ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"画像沉淀失败 {type(e).__name__}: {e}"},
                          ensure_ascii=False)


AGENT_TOOLS = [search_knowledge, search_kols, get_deal_status,
               match_kols_for_requirement, create_proposal,
               update_proposal_status, create_followup, request_deal_change,
               sediment_case, save_trait]
