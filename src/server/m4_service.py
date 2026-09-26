"""M4 业务服务：经验资产(party_traits) 两阶段 + 入库暂存人工确认 + 对齐问题清单。

原则（规格锁定）：
- 入库必经人工确认（staging → confirmed 才落正式表）
- 查询 traits 只列 verified=TRUE 条目原文，不做二次生成
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402
from server.service import new_id  # noqa: E402


# ============================================================ E: party_traits
class TraitService:
    """阶段一抽取入库（经人工确认 verified=TRUE）；阶段二只列原文。"""

    CATEGORIES = ("沟通偏好", "改稿态度", "排期习惯", "付款要求", "内容尺度", "其他")

    def add(self, party_type: str, party_id: str, trait_category: str,
            trait_content: str, source_quote: str = "", severity: str = "info",
            confidence: float = 1.0, source_type: str = "", verified: bool = False,
            created_by: str | None = None) -> str:
        """写入一条 trait。LLM 抽取默认 verified=False，人工确认后置 TRUE。"""
        if trait_category not in self.CATEGORIES:
            raise ValueError(f"非法分类: {trait_category}")
        tid = new_id("TR")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO party_traits
                   (trait_id, party_type, party_id, trait_category, trait_content,
                    source_quote, severity, confidence, verified, source_type, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (tid, party_type, party_id, trait_category, trait_content,
                 source_quote, severity, confidence, verified, source_type, created_by))
            conn.commit()
        return tid

    def confirm(self, trait_id: str, reviewer: str | None = None) -> bool:
        """人工确认 → verified=TRUE（只有此后才参与检索）。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE party_traits SET verified=TRUE WHERE trait_id=%s",
                        (trait_id,))
            n = cur.rowcount
            conn.commit()
        return n > 0

    def list_for_party(self, party_type: str, party_id: str,
                       verified_only: bool = True) -> list[dict]:
        """阶段二展示：只列 verified 条目原文（含 source_quote），零生成。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            sql = """SELECT trait_id, trait_category, trait_content, source_quote,
                            severity, confidence, source_type, created_at
                     FROM party_traits WHERE party_type=%s AND party_id=%s"""
            if verified_only:
                sql += " AND verified=TRUE"
            sql += " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, created_at DESC"
            cur.execute(sql, (party_type, party_id))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def pending_review(self, limit: int = 50) -> list[dict]:
        """待人工确认队列（confidence 低的排前）。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT trait_id, party_type, party_id, trait_category,
                                  trait_content, source_quote, severity, confidence,
                                  source_type, created_at
                           FROM party_traits WHERE verified=FALSE
                           ORDER BY confidence ASC, created_at DESC LIMIT %s""", (limit,))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]


# ============================================================ D: 入库暂存
class IngestService:
    """原始输入 → LLM 抽取（带置信度）→ 人工确认 → 双写（PG 正式表 + 向量库）。"""

    def stage(self, suggested_kind: str, source_type: str, raw_content: str,
              extracted: list[dict], created_by: str | None = None) -> str:
        """extracted: [{field, value, confidence}]，低置信度由前端高亮。"""
        sid = new_id("ST")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO ingest_staging
                   (staging_id, suggested_kind, source_type, raw_content,
                    extracted, status, created_by)
                   VALUES (%s,%s,%s,%s,%s,'pending',%s)""",
                (sid, suggested_kind, source_type, raw_content,
                 json.dumps(extracted, ensure_ascii=False), created_by))
            conn.commit()
        return sid

    def get(self, staging_id: str) -> dict | None:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM ingest_staging WHERE staging_id=%s", (staging_id,))
            if not cur.description:
                return None
            names = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                return None
            d = dict(zip(names, row))
            d["extracted"] = json.loads(d["extracted"]) if isinstance(d["extracted"], str) else d["extracted"]
            return d

    def pending(self, limit: int = 50) -> list[dict]:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT staging_id, suggested_kind, source_type,
                                  LEFT(raw_content, 80) AS raw_preview, status, created_at
                           FROM ingest_staging WHERE status='pending'
                           ORDER BY created_at DESC LIMIT %s""", (limit,))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def confirm(self, staging_id: str, final_fields: dict,
                reviewer: str | None = None) -> dict:
        """人工确认后的落库动作。final_fields 为复核修正后的最终字段。

        双写：① 结构化字段写正式表 ② 原文文本块向量化（交由上层调用
        starmoyu.ingest 的单篇入向量库，此处返回建议的向量化文本）。
        """
        st = self.get(staging_id)
        if not st:
            raise ValueError(f"暂存记录不存在: {staging_id}")
        if st["status"] != "pending":
            raise ValueError(f"该记录已处理: {st['status']}")

        kind = st["suggested_kind"]
        written_id: str | None = None
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            if kind == "kol":
                kid = final_fields.get("kol_id") or new_id("K")
                cur.execute(
                    """INSERT INTO kol_profile
                       (kol_id, kol_name, platform, category, sub_category, tier,
                        fans_count, interact_rate, price_21_60s, avg_views)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (kol_id) DO NOTHING""",
                    (kid, final_fields.get("kol_name"), final_fields.get("platform"),
                     final_fields.get("category"), final_fields.get("sub_category"),
                     final_fields.get("tier"), final_fields.get("fans_count"),
                     final_fields.get("interact_rate"), final_fields.get("price_21_60s"),
                     final_fields.get("avg_views")))
                written_id = kid
            elif kind == "feedback":
                # 商单反馈 → deal_followup（deal 必须已存在）
                fid_q = cur.execute("SELECT coalesce(max(id),0)+1 FROM deal_followup")
                fid = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO deal_followup
                       (id, deal_id, stage_from, stage_to, note, operator, created_at, action_type)
                       VALUES (%s,%s,'-','-',%s,%s,now(),'反馈入库')""",
                    (fid, final_fields.get("deal_id"), final_fields.get("note", ""),
                     reviewer or "admin"))
                written_id = str(fid)
            else:  # deal 等其他 kind 先到暂存确认，正式写库随 M5 扩展
                raise ValueError(f"kind={kind} 的正式落库尚未开放，请先在暂存区确认字段")
            cur.execute("""UPDATE ingest_staging
                           SET status='confirmed', reviewed_by=%s, reviewed_at=now()
                           WHERE staging_id=%s""", (reviewer, staging_id))
            conn.commit()
        return {"kind": kind, "written_id": written_id,
                "vectorize_text": st["raw_content"]}  # 上层拿去走向量入库

    def reject(self, staging_id: str, reviewer: str | None = None) -> bool:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("""UPDATE ingest_staging
                           SET status='rejected', reviewed_by=%s, reviewed_at=now()
                           WHERE staging_id=%s AND status='pending'""",
                        (reviewer, staging_id))
            n = cur.rowcount
            conn.commit()
        return n > 0
