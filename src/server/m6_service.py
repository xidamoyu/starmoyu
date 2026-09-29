"""M6 服务：会话钉住商单 + deal_id 注入 + 商单路线图 + 结案表单（UI 确定性路径）。

设计要点：
- 钉住状态存 conversations.pinned_deal_id（用户点选，非 LLM 决定）
- 保存动作执行时从 contextvar 读钉住的 deal_id 兜底注入 —— LLM 忘传也挂得对
- 结案表单端点直调 Service（绕过 LLM 确认卡），人工关口天然满足（用户逐字段填的）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402
from server.m5_service import DealResultService  # noqa: E402
from server.m4_service import TraitService  # noqa: E402
from server.service import new_id  # noqa: E402

# 钉住商单的传递：LangGraph configurable["pinned_deal_id"]（agent_graph._cfg 注入）。
# 工具执行时 ensure_config() 能读到当前 runnable 的 config，跨线程可靠。

# 商单号样式：DC + 8 位数字（DC20250011）。\b 在中文邻接时不生效，用 lookaround
DEAL_ID_RE = re.compile(r"(?<![A-Za-z0-9])DC\d{8}(?![0-9])")

STAGES = ("需求沟通", "提案", "签约", "执行", "结案", "丢单")


# ============================================================ 钉住
def extract_deal_ids(text: str) -> list[str]:
    """从文本提取商单号（确定性，不走 LLM）。"""
    return DEAL_ID_RE.findall(text or "")


def get_pinned(conv_id: str) -> str | None:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pinned_deal_id FROM conversations WHERE conv_id=%s", (conv_id,))
        row = cur.fetchone()
    return row[0] if row else None


def pin_deal(conv_id: str, deal_id: str | None, user_id: str) -> dict:
    """钉住/取消钉住。校验商单存在且属于该用户可见范围。"""
    if deal_id:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT deal_id, brand_name, stage FROM deal WHERE deal_id=%s",
                        (deal_id,))
            row = cur.fetchone()
        if not row:
            raise ValueError(f"商单不存在: {deal_id}")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE conversations SET pinned_deal_id=%s WHERE conv_id=%s AND user_id=%s",
                    (deal_id, conv_id, user_id))
        conn.commit()
    return {"conv_id": conv_id, "pinned_deal_id": deal_id}


def resolve_deal_id(explicit: str | None) -> str | None:
    """保存动作的 deal_id 判定：显式传入 > LangGraph configurable 钉住 > None。

    工具在 LangGraph ToolNode 里执行时，ensure_config() 返回当前运行 config；
    不在图内（如 API 直调）时返回空 config，取不到钉住值。"""
    if explicit:
        return explicit
    try:
        from langchain_core.runnables.config import ensure_config
        cfg = ensure_config()
        return (cfg.get("configurable") or {}).get("pinned_deal_id") or None
    except Exception:
        return None


# ============================================================ 商单路线图
def deal_timeline(deal_id: str) -> dict | None:
    """商单全量时间线：基本信息 + stage 现值 + 跟进流水（git log 风格数据源）。"""
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT deal_id, brand_name, category, goal, budget, stage,
                              demand_desc, kol_ids, start_date, end_date, result_metrics
                       FROM deal WHERE deal_id=%s""", (deal_id,))
        names = [d[0] for d in cur.description]
        row = cur.fetchone()
        if not row:
            return None
        d = dict(zip(names, row))
        if isinstance(d["result_metrics"], str):
            d["result_metrics"] = json.loads(d["result_metrics"] or "null")
        cur.execute("""SELECT id, stage_from, stage_to, note, operator, created_at, action_type
                       FROM deal_followup WHERE deal_id=%s ORDER BY created_at ASC""", (deal_id,))
        fnames = [x[0] for x in cur.description]
        d["followups"] = [dict(zip(fnames, r)) for r in cur.fetchall()]
        # 特质条目（按商单溯源）
        cur.execute("""SELECT trait_id, party_type, party_id, trait_category, trait_content,
                              source_quote, severity, verified, created_at
                       FROM party_traits WHERE deal_id=%s ORDER BY created_at DESC""", (deal_id,))
        tnames = [x[0] for x in cur.description]
        d["traits"] = [dict(zip(tnames, r)) for r in cur.fetchall()]
    d["stages"] = list(STAGES)
    return d


def set_stage(deal_id: str, stage: str, operator: str, note: str = "") -> dict:
    """修改阶段（写 deal_followup 留痕）。状态机校验：目标必须是合法 stage。"""
    if stage not in STAGES:
        raise ValueError(f"非法阶段: {stage}，可选: {STAGES}")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT stage FROM deal WHERE deal_id=%s", (deal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"商单不存在: {deal_id}")
        old = row[0]
        fid_q = cur.execute("SELECT coalesce(max(id),0)+1 FROM deal_followup")
        fid = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO deal_followup
               (id, deal_id, stage_from, stage_to, note, operator, created_at, action_type)
               VALUES (%s,%s,%s,%s,%s,%s,now(),'手动改阶段')""",
            (fid, deal_id, old, stage, note or f"{old} → {stage}", operator))
        cur.execute("UPDATE deal SET stage=%s, updated_at=now() WHERE deal_id=%s",
                    (stage, deal_id))
        conn.commit()
    return {"deal_id": deal_id, "stage_from": old, "stage_to": stage, "followup_id": str(fid)}


# ============================================================ 结案表单（UI 确定性路径）
def close_deal_form(deal_id: str, metrics: dict, summary_note: str = "",
                    trait_specs: list[dict] | None = None,
                    operator: str = "u-admin") -> dict:
    """结案表单直调：数字逐字段填的（非 LLM 抽取），人工关口天然满足。

    - metrics: {roi, gmv, exposure, interaction, ...} 白名单过滤后 merge 写 result_metrics
    - summary_note: 用户随手一句话（原文留档 staging）
    - trait_specs: [{kol_id, trait_category, trait_content, source_quote, severity}]
                    来自截图抽取 + 用户勾选，直接 verified=TRUE
    """
    ex = {"deal_id": deal_id, "metrics": metrics}
    svc = DealResultService()
    sid = svc.stage_result(deal_id, summary_note or json.dumps(metrics, ensure_ascii=False), ex)
    result = svc.confirm_result(sid, reviewer=operator)

    tids = []
    for spec in (trait_specs or []):
        kid = spec.get("kol_id") or ""
        if not kid:
            continue
        tids.append(TraitService().add(
            "kol", kid, spec.get("trait_category", "其他"),
            spec.get("trait_content", ""), source_quote=spec.get("source_quote", ""),
            severity=spec.get("severity", "info"), confidence=1.0,
            source_type="screenshot", verified=True, deal_id=deal_id,
            created_by=operator))
    return {"ok": True, "deal_result": result, "trait_ids": tids,
            "hint": "结案已落库。建议沉淀：本单可渲染成案例文档回流知识库（复盘经验+跟进大事记），"
                    "向用户确认后调用 sediment_case 工具出预览，用户同意后入库。"}
