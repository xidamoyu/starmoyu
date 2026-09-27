"""M5 工具层：结案复盘 / Brief 解析 / 主动简报 / 品牌特质 / 待确认队列。

沿袭 m4_tools 的设计：
- 工具签名对 LLM 宽容（多别名、中英文键都认）
- save_* 工具都是「确认卡」动作：Agent 必须先展示抽取结果、用户确认后才调
- 查询侧只列原文/原值，零生成
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from server.m5_service import BriefService, BriefingService, DealResultService  # noqa: E402
from server.m4_service import TraitService  # noqa: E402
from starmoyu import storage  # noqa: E402

# 供 save_brief 确认后直接给组合建议（内部调用 agent_tools.match_kols_for_requirement）
from agent_tools import match_kols_for_requirement as _match_impl  # noqa: E402


# ============================================================ F1: 结案复盘
@tool
def save_deal_result(
    deal_id: str = "",
    raw_text: str = "",
    result_text: str = "",
    metrics: Any = None,
) -> str:
    """结案复盘入库（唯一正式通道）：把商单效果数据（ROI/GMV/曝光/互动等）写入商单档案。

    【这是效果数据入库的唯一正式通道】用户已确认结案数据时，必须调用本工具完成归档，
    用 create_followup 记笔记不算入库。未经确认时禁止调用。
    Args:
        deal_id: 商单编号（如 DC20260001）；不知道就留空，配合商单名/品牌让用户确认
        raw_text: 用户粘贴的结案原文（或 result_text）
        metrics: 效果指标 dict 或 JSON 字符串，支持中文键：
                 {"roi"/"ROI": 2.1, "gmv"/"GMV": 150000, "exposure"/"曝光": 5000000,
                  "interaction"/"互动": 80000, "cpm": 44.2}
    """
    try:
        text = raw_text or result_text
        m = metrics if isinstance(metrics, dict) else json.loads(metrics or "{}")
        # 中文键 → 标准键
        alias = {"roi": "roi", "r oi": "roi", "gmv": "gmv", "曝光": "exposure",
                 "exposure": "exposure", "互动": "interaction", "互动量": "interaction",
                 "interaction": "interaction", "cpm": "cpm", "播放": "views",
                 "views": "views", "点赞": "likes", "likes": "likes",
                 "评论": "comments", "转发": "shares", "转化": "conversion"}
        std = {}
        for k, v in m.items():
            key = alias.get(str(k).lower().strip()) or alias.get(str(k).strip())
            if key and v is not None:
                std[key] = v
        if not deal_id:
            return json.dumps({"error": "缺少 deal_id。请用户确认商单编号，或先调 get_deal_status 查询"},
                              ensure_ascii=False)
        if not std:
            return json.dumps({"error": "没有可识别的效果指标（支持 ROI/GMV/曝光/互动/CPM/播放等）"},
                              ensure_ascii=False)

        svc = DealResultService()
        ex = {"deal_id": deal_id, "metrics": std}
        sid = svc.stage_result(deal_id, text or json.dumps(std, ensure_ascii=False), ex)
        res = svc.confirm_result(sid, reviewer="agent-card")
        return json.dumps({"ok": True, **res,
                           "message": f"商单 {deal_id} 已结案归档：{res['written']}"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


@tool
def get_deal_result(deal_id: str = "") -> str:
    """查询商单的结案效果数据（ROI/GMV/曝光/互动等原值，不做二次加工）。

    【强制】用户问某商单"效果怎么样/ROI多少/GMV多少/结案数据"时，必须调用本工具（而不是 get_deal_status）。
    """
    try:
        if not deal_id:
            return json.dumps({"error": "缺少 deal_id"}, ensure_ascii=False)
        d = DealResultService().get_result(deal_id)
        if not d:
            return json.dumps({"error": f"商单不存在: {deal_id}"}, ensure_ascii=False)
        return json.dumps(d, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


# ============================================================ F2: Brief 解析
@tool
def save_brief(
    raw_text: str = "",
    category: str = "",
    budget: Any = None,
    kol_count: Any = None,
    schedule: str = "",
    requirements: Any = None,
    deal_id: str = "",
) -> str:
    """把甲方 brief 原文建为新方案草稿（唯一正式通道）。

    【这是 brief 建稿的唯一正式通道】用户确认需求卡后必须调用本工具；
    create_proposal 不走确认流程、不留版本痕，确认卡流程中禁止用它替代。
    未经用户确认时禁止调用本工具。
    Args:
        raw_text: brief 原文
        category: 品类（如 美妆/3C数码/食品饮料）
        budget: 总预算（数字，单位元）
        kol_count: 达人人数（数字，可选）
        schedule: 档期要求（如 下周三前发布）
        requirements: 补充要求（list 或字符串，如 不能接纯口播/必须提前一周给brief）
        deal_id: 已有商单号（可选；新单不传）
    """
    try:
        if not raw_text:
            return json.dumps({"error": "缺少 brief 原文"}, ensure_ascii=False)
        if not category or budget in (None, ""):
            return json.dumps({"error": "缺少品类或预算，请先向用户确认"}, ensure_ascii=False)
        ex = {"category": category, "budget": budget,
              "kol_count": kol_count, "schedule": schedule,
              "requirements": requirements if isinstance(requirements, list)
              else ([requirements] if requirements else []),
              "deal_id": deal_id}
        sid = BriefService().stage_brief(raw_text, ex)
        res = BriefService().confirm_brief(sid, reviewer="agent-card")
        # 顺手给组合建议（复用现有匹配器，确定性排序）
        try:
            suggestion = json.loads(_match_impl.invoke({"category": str(category),
                                                        "budget": int(budget),
                                                        "kol_count": int(kol_count or 3)}))
        except Exception:
            suggestion = {}
        return json.dumps({"ok": True, **res, "suggestion": suggestion,
                           "message": f"已建方案草稿 {res['proposal_id']}，并给出达人组合建议"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


# ============================================================ F3: 主动简报
@tool
def get_today_briefing() -> str:
    """今日工作简报：档期临期 / 待审批方案 / 3天未跟进商单 / 黑名单撞单。

    用户开场打招呼（你好/今天有什么事）或问「今天该干什么/有什么要处理的」时【必须】先调本工具。
    """
    try:
        data = BriefingService().today()
        n = sum(len(v) for v in data.values())
        return json.dumps({"ok": True, "total": n, **data}, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


# ============================================================ F4: 品牌特质
@tool
def get_brand_traits(brand_name: str = "") -> str:
    """查询某品牌的沉淀特质（回款习惯/brief 风格等 verified 原文条目）。

    评估品牌合作、回复品牌询价前应先调用（与达人特质同一原则：只列原文）。
    """
    try:
        if not brand_name:
            return json.dumps({"error": "缺少 brand_name"}, ensure_ascii=False)
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT brand_id FROM brand WHERE brand_name ILIKE %s LIMIT 1",
                        (brand_name,))
            row = cur.fetchone()
        if not row:
            return json.dumps({"brand_name": brand_name, "n": 0, "items": [],
                               "note": "品牌不存在"}, ensure_ascii=False)
        bid = row[0]
        rows = TraitService().list_for_party("brand", bid, verified_only=True)
        return json.dumps({"brand_id": bid, "brand_name": brand_name,
                           "n": len(rows), "items": rows},
                          ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


@tool
def save_brand_traits(
    brand_name: str = "",
    raw_text: str = "",
    traits: Any = None,
) -> str:
    """品牌合作经验沉淀入库（回款慢/brief 爱改等）。确认卡动作：先展示后调用。

    Args:
        brand_name: 品牌名（须已在品牌库中）
        raw_text: 原始内容
        traits: 同 save_interaction 的 traits 格式（分类/内容/原文引用/严重度）
    """
    try:
        if not brand_name:
            return json.dumps({"error": "缺少 brand_name"}, ensure_ascii=False)
        if not raw_text:
            return json.dumps({"error": "缺少 raw_text"}, ensure_ascii=False)
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT brand_id, brand_name FROM brand WHERE brand_name ILIKE %s LIMIT 1",
                        (brand_name,))
            row = cur.fetchone()
        if not row:
            return json.dumps({"error": f"品牌不存在: {brand_name}",
                               "hint": "先调 search_knowledge 或让用户确认品牌名"},
                              ensure_ascii=False)
        bid, bname = row
        lst = traits if isinstance(traits, list) else json.loads(traits or "[]")
        from m4_tools import _norm_trait
        tids = []
        for t in lst:
            nt = _norm_trait(t)
            if not nt:
                continue
            tids.append(TraitService().add(
                "brand", bid, nt["trait_category"], nt["trait_content"],
                source_quote=nt["source_quote"], severity=nt["severity"],
                confidence=nt["confidence"], source_type="chat",
                verified=True, created_by="agent"))
        return json.dumps({"ok": True, "brand_id": bid, "brand_name": bname,
                           "trait_ids": tids,
                           "message": f"已沉淀 {len(tids)} 条品牌特质"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


# ============================================================ M3: 待确认队列
@tool
def get_pending_traits() -> str:
    """查询待人工确认的特质条目（verified=FALSE，低置信度排前）。

    用户问「有没有待确认的内容/入库确认队列」时调用；确认动作需用户明示。
    """
    try:
        rows = TraitService().pending_review()
        return json.dumps({"n": len(rows), "items": rows},
                          ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


M5_TOOLS = [save_deal_result, get_deal_result, save_brief, get_today_briefing,
            get_brand_traits, save_brand_traits, get_pending_traits]
