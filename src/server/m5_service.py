"""M5 服务层：结案复盘 / Brief 解析 / 主动简报。

原则沿袭 M4：
- 一切写库走 Service；LLM 抽取先落 staging（pending），确认后才动正式表
- 数字必须来自原文，查询侧只列原文/原值，不二次生成
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402
from server.service import new_id  # noqa: E402


# ============================================================ F1: 结案复盘
class DealResultService:
    """商单效果数据回流：staging(pending) → 确认 → deal.result_metrics + stage + 跟进留痕。"""

    ALLOWED_METRICS = ("roi", "gmv", "exposure", "interaction", "cpm", "cpe",
                       "views", "likes", "comments", "shares", "conversion")

    def stage_result(self, deal_id: str, raw_text: str, extracted: dict,
                     created_by: str | None = None) -> str:
        sid = new_id("ST")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO ingest_staging
                   (staging_id, suggested_kind, source_type, raw_content,
                    extracted, status, created_by)
                   VALUES (%s,'deal_result','paste',%s,%s,'pending',%s)""",
                (sid, raw_text, json.dumps(extracted, ensure_ascii=False), created_by))
            conn.commit()
        return sid

    def get_staged(self, staging_id: str) -> dict | None:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM ingest_staging WHERE staging_id=%s", (staging_id,))
            names = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                return None
            d = dict(zip(names, row))
            d["extracted"] = json.loads(d["extracted"]) if isinstance(d["extracted"], str) else d["extracted"]
            return d

    def confirm_result(self, staging_id: str, reviewer: str | None = None) -> dict:
        """确认后原子写：deal.result_metrics（merge，不覆盖未提及字段）+ stage='结案' + 跟进留痕。"""
        st = self.get_staged(staging_id)
        if not st:
            raise ValueError(f"暂存记录不存在: {staging_id}")
        if st["status"] != "pending":
            raise ValueError(f"该记录已处理: {st['status']}")
        ex = st["extracted"] or {}
        deal_id = ex.get("deal_id")
        metrics = {k: v for k, v in (ex.get("metrics") or {}).items()
                   if k in self.ALLOWED_METRICS and v is not None}
        if not deal_id:
            raise ValueError("extracted 缺 deal_id")
        if not metrics:
            raise ValueError("extracted 缺可入库的效果指标")

        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT result_metrics, stage FROM deal WHERE deal_id=%s", (deal_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"商单不存在: {deal_id}")
            old = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
            merged = {**old, **metrics}
            fid_q = cur.execute("SELECT coalesce(max(id),0)+1 FROM deal_followup")
            fid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO deal_followup
                   (id, deal_id, stage_from, stage_to, note, operator, created_at, action_type)
                   VALUES (%s,%s,%s,'结案',%s,%s,now(),'结案复盘')""",
                (fid, deal_id, row[1], json.dumps(metrics, ensure_ascii=False), reviewer or "agent"))
            cur.execute(
                """UPDATE deal SET result_metrics=%s::jsonb, stage='结案', updated_at=now()
                   WHERE deal_id=%s""", (json.dumps(merged, ensure_ascii=False), deal_id))
            cur.execute(
                """UPDATE ingest_staging SET status='confirmed', reviewed_by=%s, reviewed_at=now()
                   WHERE staging_id=%s""", (reviewer or "agent", staging_id))
            conn.commit()
        return {"deal_id": deal_id, "written": merged, "followup_id": str(fid),
                "staging_id": staging_id,
                "hint": "结案已落库。建议沉淀：可把本单渲染成案例文档回流知识库，"
                        "向用户确认后调用 sediment_case 工具出预览，用户同意后入库。"}

    def get_result(self, deal_id: str) -> dict | None:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT deal_id, brand_name, category, budget, stage,
                                  result_metrics, kol_ids
                           FROM deal WHERE deal_id=%s""", (deal_id,))
            names = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                return None
            d = dict(zip(names, row))
            if isinstance(d["result_metrics"], str):
                d["result_metrics"] = json.loads(d["result_metrics"] or "null")
            return d

    def kol_effect_history(self, kol_id: str) -> list[dict]:
        """某达人参与过的结案商单及其效果——选号时引用（只列原值）。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT deal_id, brand_name, category, budget, result_metrics
                   FROM deal
                   WHERE stage='结案' AND result_metrics IS NOT NULL
                     AND kol_ids @> %s::jsonb
                   ORDER BY updated_at DESC NULLS LAST LIMIT 10""",
                (json.dumps([kol_id]),))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]


# ============================================================ F2: Brief 解析
class BriefService:
    """甲方 brief → staging(kind='brief') → 确认 → proposals 草稿。"""

    REQUIRED = ("category", "budget", "kol_count")

    def stage_brief(self, raw_text: str, extracted: dict,
                    created_by: str | None = None) -> str:
        sid = new_id("ST")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO ingest_staging
                   (staging_id, suggested_kind, source_type, raw_content,
                    extracted, status, created_by)
                   VALUES (%s,'brief','paste',%s,%s,'pending',%s)""",
                (sid, raw_text, json.dumps(extracted, ensure_ascii=False), created_by))
            conn.commit()
        return sid

    def confirm_brief(self, staging_id: str, reviewer: str | None = None) -> dict:
        """确认后：建 proposals 草稿（version=1, status=draft）。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM ingest_staging WHERE staging_id=%s", (staging_id,))
            names = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                raise ValueError(f"暂存记录不存在: {staging_id}")
            st = dict(zip(names, row))
            if st["status"] != "pending":
                raise ValueError(f"该记录已处理: {st['status']}")
            ex = st["extracted"] if isinstance(st["extracted"], dict) else json.loads(st["extracted"] or "{}")
            if not ex.get("category") or not ex.get("budget"):
                raise ValueError("brief 抽取缺 category/budget，不能建方案")
            pid = new_id("PP")
            summary = {
                "category": ex.get("category"), "budget": ex.get("budget"),
                "kol_count": ex.get("kol_count"),
                "schedule": ex.get("schedule"), "requirements": ex.get("requirements"),
            }
            cur.execute(
                """INSERT INTO proposals
                   (proposal_id, deal_id, requirement_text, content, status, created_by,
                    created_at, updated_at)
                   VALUES (%s,%s,%s,%s,'draft',%s,now(),now())""",
                (pid, ex.get("deal_id") or None,
                 st["raw_content"], json.dumps(summary, ensure_ascii=False),
                 "u-admin" if (reviewer or "agent") == "agent" else "u-admin"))
            cur.execute(
                """INSERT INTO proposal_versions
                   (version_id, proposal_id, version_no, content, comment, changed_by, created_at)
                   VALUES (%s,%s,1,%s,'brief 解析建稿',%s,now())""",
                (new_id("PV"), pid, json.dumps(summary, ensure_ascii=False), "u-admin"))
            cur.execute(
                """UPDATE ingest_staging SET status='confirmed', reviewed_by=%s, reviewed_at=now()
                   WHERE staging_id=%s""", (reviewer or "agent", staging_id))
            conn.commit()
        return {"proposal_id": pid, "summary": summary, "staging_id": staging_id}


# ============================================================ F3: 主动简报
class BriefingService:
    """开场简报：临期档期 / 待审批 / 失联商单 / 黑名单撞单。只列事实，不生成。"""

    def today(self) -> dict:
        out: dict = {}
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            # 1) 7 天内到期档期（在途单的达人档期）
            cur.execute(
                """SELECT d.deal_id, d.brand_name, d.stage, k.kol_id, k.kol_name,
                          k.exclusive_until
                   FROM deal d
                   JOIN kol_profile k ON k.kol_id = ANY(
                       SELECT jsonb_array_elements_text(d.kol_ids))
                   WHERE d.stage IN ('需求沟通','提案','签约','执行')
                     AND k.exclusive_until IS NOT NULL
                     AND k.exclusive_until <= (CURRENT_DATE + INTERVAL '7 days')
                   ORDER BY k.exclusive_until LIMIT 10""")
            names = [d[0] for d in cur.description]
            out["档期临期"] = [dict(zip(names, r)) for r in cur.fetchall()]
            # 2) 待审批方案
            cur.execute(
                """SELECT proposal_id, deal_id, LEFT(requirement_text, 60) AS req_preview,
                          created_at
                   FROM proposals WHERE status='draft' ORDER BY created_at DESC LIMIT 10""")
            names = [d[0] for d in cur.description]
            out["待审批方案"] = [dict(zip(names, r)) for r in cur.fetchall()]
            # 3) 在途但 3 天没跟进
            cur.execute(
                """SELECT d.deal_id, d.brand_name, d.stage, MAX(f.created_at) AS last_followup
                   FROM deal d LEFT JOIN deal_followup f ON f.deal_id = d.deal_id
                   WHERE d.stage IN ('需求沟通','提案','签约','执行')
                   GROUP BY d.deal_id, d.brand_name, d.stage
                   HAVING COALESCE(MAX(f.created_at), d.created_at) < now() - INTERVAL '3 days'
                   ORDER BY last_followup NULLS FIRST LIMIT 10""")
            names = [d[0] for d in cur.description]
            out["3天未跟进"] = [dict(zip(names, r)) for r in cur.fetchall()]
            # 4) 在途单里的黑名单达人
            cur.execute(
                """SELECT d.deal_id, d.brand_name, d.stage, k.kol_id, k.kol_name
                   FROM deal d
                   JOIN kol_profile k ON k.kol_id = ANY(
                       SELECT jsonb_array_elements_text(d.kol_ids))
                   AND d.stage IN ('需求沟通','提案','签约','执行') AND k.blacklist = 'true'
                   LIMIT 10""")
            names = [d[0] for d in cur.description]
            out["黑名单撞单"] = [dict(zip(names, r)) for r in cur.fetchall()]
        return out
