"""M7b 服务层：商单字段变更审批。

链路: 对话内 Agent 识别商单内容变更 → request_deal_change 工具 → 本 Service 建申请(pending)
      → 审批中心批准 → approve() 自动改 deal 表 + deal_followup 留痕
      → 驳回 → reject() 仅标记状态。
一商单可挂多条变更(一商单号对应多个修改审批)。
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

# 允许变更的字段白名单 → (deal 列名, 是否数值)
ALLOWED_FIELDS = {
    "budget": ("budget", True),
    "owner": ("owner", False),
    "stage": ("stage", False),
    "demand_desc": ("demand_desc", False),
    "goal": ("goal", False),
    "cpm_target": ("cpm_target", True),
    "platform_req": ("platform_req", False),
}
STATUS = ("pending", "approved", "rejected")


class DealChangeService:
    def create(self, deal_id: str, field_name: str, new_value: str,
               change_summary: str = "", conv_id: str = "",
               created_by: str | None = None) -> dict:
        if field_name not in ALLOWED_FIELDS:
            raise ValueError(f"不允许变更字段: {field_name}，可选: {list(ALLOWED_FIELDS)}")
        col, _ = ALLOWED_FIELDS[field_name]
        rid = new_id("CR")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {col} FROM deal WHERE deal_id=%s", (deal_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"商单不存在: {deal_id}")
            old_value = "" if row[0] is None else str(row[0])
            cur.execute(
                """INSERT INTO deal_change_requests
                   (request_id, deal_id, conv_id, field_name, old_value, new_value,
                    change_summary, status, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s)""",
                (rid, deal_id, conv_id, field_name, old_value, str(new_value),
                 change_summary, created_by))
            conn.commit()
        return self.get(rid)

    def get(self, request_id: str) -> dict | None:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM deal_change_requests WHERE request_id=%s", (request_id,))
            names = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(names, row)) if row else None

    def list(self, status: str = "", deal_id: str = "", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM deal_change_requests WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status=%s"; args.append(status)
        if deal_id:
            sql += " AND deal_id=%s"; args.append(deal_id)
        sql += " ORDER BY created_at DESC LIMIT %s"; args.append(min(limit, 300))
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def approve(self, request_id: str, reviewer: str, comment: str = "") -> dict:
        req = self._require_pending(request_id)
        col, is_num = ALLOWED_FIELDS[req["field_name"]]
        new_val = req["new_value"]
        if is_num:
            try:
                new_val = float(new_val)
            except ValueError:
                raise ValueError(f"字段 {req['field_name']} 需数值, 收到: {new_val!r}")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE deal SET {col}=%s, updated_at=now() WHERE deal_id=%s",
                        (new_val, req["deal_id"]))
            self._log_followup(cur, req, reviewer, f"变更批准: {req['field_name']} {req['old_value']}→{req['new_value']} {comment}".strip())
            self._set_status(cur, request_id, "approved", reviewer)
            conn.commit()
        return {"ok": True, "request_id": request_id, "deal_updated": {req["field_name"]: new_val}}

    def reject(self, request_id: str, reviewer: str, comment: str = "") -> dict:
        req = self._require_pending(request_id)
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            self._log_followup(cur, req, reviewer, f"变更驳回: {req['field_name']} 维持 {req['old_value']} {comment}".strip())
            self._set_status(cur, request_id, "rejected", reviewer)
            conn.commit()
        return {"ok": True, "request_id": request_id, "status": "rejected"}

    # ---- internals ----
    def _require_pending(self, request_id: str) -> dict:
        req = self.get(request_id)
        if not req:
            raise ValueError(f"变更申请不存在: {request_id}")
        if req["status"] != "pending":
            raise ValueError(f"申请已处理(status={req['status']}), 不能重复审批")
        return req

    def _set_status(self, cur, request_id: str, status: str, reviewer: str) -> None:
        cur.execute(
            "UPDATE deal_change_requests SET status=%s, reviewed_by=%s, reviewed_at=now() WHERE request_id=%s",
            (status, reviewer, request_id))

    def _log_followup(self, cur, req: dict, operator: str, note: str) -> None:
        cur.execute("SELECT coalesce(max(id),0)+1 FROM deal_followup")
        fid = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO deal_followup (id, deal_id, stage_from, stage_to, note, operator, created_at, action_type)
               VALUES (%s,%s,NULL,NULL,%s,%s,now(),'字段变更审批')""",
            (fid, req["deal_id"], note, operator))
