"""M4 沉淀流工具：save_interaction（一步入库） / list_kol_traits（原文查询）。

设计原则（用户已确认）：
- 一次粘贴 → Agent 抽取并在回复里展示确认卡 → 用户确认 → 调本工具原子写三处
- 人工确认关口在【对话层】：Agent 必须先展示抽取结果、得到用户肯定后才调用本工具
  （系统提示词硬约束 + 验收脚本断言确认前 traits=0）
- 工具签名对 LLM 宽容：参数全部可选、中英文键名都认，避免 schema 校验失败
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

from server.m4_service import IngestService, TraitService  # noqa: E402
from server.service import FollowupService  # noqa: E402
from starmoyu import storage  # noqa: E402

# 中英文键名别名映射（LLM 传哪种都认）
_CAT_KEYS = ("trait_category", "category", "分类", "类别")
_CONTENT_KEYS = ("trait_content", "content", "内容", "条目")
_QUOTE_KEYS = ("source_quote", "quote", "原文引用", "原文", "引用")
_SEV_KEYS = ("severity", "严重度", "级别")


def _norm_trait(t: Any) -> dict | None:
    if not isinstance(t, dict):
        return None
    content = next((t[k] for k in _CONTENT_KEYS if t.get(k)), "")
    if not content:
        return None
    return {
        "trait_category": next((t[k] for k in _CAT_KEYS if t.get(k)), "其他"),
        "trait_content": content,
        "source_quote": next((t[k] for k in _QUOTE_KEYS if t.get(k)), content),
        "severity": next((t[k] for k in _SEV_KEYS if t.get(k)), "info"),
        "confidence": float(t.get("confidence", t.get("置信度", 1.0))),
    }


def _resolve_kol(kol_id: str, kol_name: str):
    """返回 (resolved, candidates)。

    - kol_id：精确（ILIKE）匹配，命中即返回 (kol, None)
    - kol_name：先精确；未命中再模糊。
      * 模糊唯一命中 → 直接返回
      * 模糊多命中 → 返回 (None, 候选列表)，由 Agent 向用户澄清，禁止瞎猜
    """
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        if kol_id:
            cur.execute("SELECT kol_id, kol_name FROM kol_profile WHERE kol_id ILIKE %s",
                        (kol_id,))
            row = cur.fetchone()
            return ((row[0], row[1]), None) if row else (None, None)
        if kol_name:
            cur.execute("SELECT kol_id, kol_name FROM kol_profile WHERE kol_name=%s",
                        (kol_name,))
            row = cur.fetchone()
            if row:
                return (row[0], row[1]), None
            cur.execute("""SELECT kol_id, kol_name, fans_count FROM kol_profile
                           WHERE kol_name ILIKE %s
                           ORDER BY fans_count DESC NULLS LAST LIMIT 5""",
                        (f"%{kol_name}%",))
            rows = cur.fetchall()
            if len(rows) == 1:
                return (rows[0][0], rows[0][1]), None
            if len(rows) > 1:
                return None, [{"kol_id": r[0], "kol_name": r[1], "fans": r[2]}
                              for r in rows]
        return None, None


@tool
def save_interaction(
    kol_id: str = "",
    kol_name: str = "",
    raw_text: str = "",
    interaction_text: str = "",
    content: str = "",
    note: str = "",
    deal_id: str = "",
    traits: Any = None,
) -> str:
    """工单收尾沉淀入库：把本次合作的聊天记录/口述经验原子写入三处（原文留档 + 达人特质 + 跟进记录）。

    【调用前提】必须已向用户展示抽取条目并得到用户明确同意（"确认/可以/没问题"）。
    未经确认禁止调用本工具。
    【注意】deal_id 是可选的——用户没有提供商单号时直接留空，绝不要为了要商单号而打断入库流程。

    Args:
        kol_id: 达人编号（如 K0088）；如果只知道昵称，也可只传 kol_name
        kol_name: 达人昵称（与 kol_id 二选一）
        raw_text: 原始聊天记录/口述内容（也可用 interaction_text/content 传）
        interaction_text: 同 raw_text（别名）
        content: 同 raw_text（别名）
        note: 本单跟进摘要（可选）
        deal_id: 关联商单号（可选；没有就留空，不要追问用户）
        traits: 特质列表（list 或 JSON 字符串）。每条支持中英文键:
                {"trait_category"/"分类": "改稿态度|沟通偏好|排期习惯|付款要求|内容尺度|其他",
                 "trait_content"/"内容": "...", "source_quote"/"原文引用": "...",
                 "severity"/"严重度": "critical|warning|info"}
    """
    try:
        text = raw_text or interaction_text or content
        if not text:
            return json.dumps({"error": "缺少原文：请传 raw_text（或 interaction_text/content）"},
                              ensure_ascii=False)
        resolved, candidates = _resolve_kol(kol_id, kol_name)
        if candidates:
            return json.dumps(
                {"error": f"昵称「{kol_name}」匹配到多位达人，请用户澄清是哪一位",
                 "candidates": candidates},
                ensure_ascii=False)
        if not resolved:
            ident = kol_id or kol_name or "(未提供)"
            return json.dumps(
                {"error": f"达人不存在或未指定: {ident}",
                 "hint": "先调 search_kols 确认达人编号后再试"},
                ensure_ascii=False)
        kid, kname = resolved

        # 1) 原文留档（staging 直接 confirmed——本工具即确认动作）
        sid = IngestService().stage("feedback", "chat", text, [
            {"field": "kol_id", "value": kid, "confidence": 1.0},
            {"field": "kol_name", "value": kname, "confidence": 1.0},
        ], created_by="agent")

        # 2) 达人特质（跨单复用资产）
        trait_list = traits if isinstance(traits, list) else json.loads(traits or "[]")
        tids = []
        for t in trait_list:
            nt = _norm_trait(t)
            if not nt:
                continue
            tids.append(TraitService().add(
                "kol", kid, nt["trait_category"], nt["trait_content"],
                source_quote=nt["source_quote"], severity=nt["severity"],
                confidence=nt["confidence"], source_type="chat",
                verified=True, created_by="agent"))

        # 3) 跟进记录（本单维度，可选）
        fid = None
        if deal_id and note:
            fid = FollowupService().create(deal_id, note, action_type="沉淀入库")

        # staging 置 confirmed
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("""UPDATE ingest_staging
                           SET status='confirmed', reviewed_by='user-card', reviewed_at=now()
                           WHERE staging_id=%s""", (sid,))
            conn.commit()

        return json.dumps({
            "ok": True, "staging_id": sid, "kol_id": kid, "kol_name": kname,
            "trait_ids": tids, "followup_id": fid,
            "message": f"已沉淀 {len(tids)} 条达人特质"
                       + (" + 1 条跟进记录" if fid else "")
                       + f"。今后谈到 {kname} 时将自动提示这些经验。",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


@tool
def list_kol_traits(kol_id: str = "", kol_name: str = "") -> str:
    """查询某达人的沉淀特质（verified 原文条目，零生成）。谈到达人时应主动调用展示。"""
    resolved, candidates = _resolve_kol(kol_id, kol_name)
    if candidates:
        return json.dumps(
            {"error": f"昵称「{kol_name}」匹配到多位达人，请向用户澄清是哪一位",
             "candidates": candidates, "n": 0, "items": []},
            ensure_ascii=False)
    if not resolved:
        return json.dumps({"kol_id": kol_id or kol_name, "n": 0, "items": [],
                           "note": "达人不存在"}, ensure_ascii=False)
    kid, kname = resolved
    rows = TraitService().list_for_party("kol", kid, verified_only=True)
    return json.dumps({"kol_id": kid, "kol_name": kname, "n": len(rows), "items": rows},
                      ensure_ascii=False, default=str)


M4_TOOLS = [save_interaction, list_kol_traits]
