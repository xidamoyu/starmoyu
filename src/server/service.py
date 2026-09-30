"""Service 层：业务写操作的唯一入口（数据沉淀的关键设计）。

规则：路由/工具/前端都不得直接写业务表，一律经过本层。
本层负责：ID 生成、时间戳、状态机校验、版本化。
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class ProposalService:
    """方案全生命周期：草稿 → 提交审批 → 通过/驳回（版本化）。"""

    # 状态机：action -> (允许的当前状态, 目标状态)
    TRANSITIONS = {
        "submit":  {"draft", "rejected"},          # 提交审批
        "approve": {"pending_review"},             # 通过
        "reject":  {"pending_review"},             # 驳回（可改后重提）
        "revise":  {"draft", "pending_review", "rejected"},
    }
    TARGET = {"submit": "pending_review", "approve": "approved",
              "reject": "rejected", "revise": None}  # revise 保持当前态并加版本

    def create_draft(self, requirement_text: str, deal_id: str | None,
                     content: str = "", created_by: str | None = None) -> str:
        pid = new_id("PP")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO proposals
                   (proposal_id, conv_id, deal_id, requirement_text, content,
                    status, created_by, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,'draft',%s,now(),now())""",
                (pid, None, deal_id, requirement_text, content or "(待生成)", created_by))
            conn.commit()
        return pid

    def update_content(self, proposal_id: str, content: str, changed_by: str | None = None,
                       comment: str = "") -> None:
        """写入正文并追加一个版本。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT coalesce(max(version_no),0) FROM proposal_versions WHERE proposal_id=%s",
                        (proposal_id,))
            vno = cur.fetchone()[0] + 1
            cur.execute("""INSERT INTO proposal_versions
                           (version_id, proposal_id, version_no, content, comment, changed_by)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (new_id("PV"), proposal_id, vno, content, comment, changed_by))
            cur.execute("UPDATE proposals SET content=%s, updated_at=now() WHERE proposal_id=%s",
                        (content, proposal_id))
            conn.commit()

    def transition(self, proposal_id: str, action: str, comment: str = "",
                   new_content: str | None = None, changed_by: str | None = None) -> bool:
        if action not in self.TRANSITIONS:
            raise ValueError(f"非法动作: {action}")
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status FROM proposals WHERE proposal_id=%s", (proposal_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"方案不存在: {proposal_id}")
            status = row[0]
            if status not in self.TRANSITIONS[action]:
                raise ValueError(f"状态 {status} 不允许执行 {action}")
            target = self.TARGET[action] or status
            if action == "revise":
                if not new_content:
                    raise ValueError("revise 必须提供 new_content")
                self.update_content(proposal_id, new_content, changed_by, comment)
            cur.execute("UPDATE proposals SET status=%s, updated_at=now() WHERE proposal_id=%s",
                        (target, proposal_id))
            conn.commit()
        return True

    def get(self, proposal_id: str) -> dict | None:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM proposals WHERE proposal_id=%s", (proposal_id,))
            if not cur.description:
                return None
            names = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(names, row)) if row else None


class FollowupService:
    """跟进动作落库（写现有 deal_followup 表，带新扩展字段）。"""

    def create(self, deal_id: str, note: str, action_type: str = "其他",
               operator_id: str | None = None) -> str:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM deal WHERE deal_id=%s", (deal_id,))
            if not cur.fetchone():
                raise ValueError(f"商单不存在: {deal_id}")
            # deal_followup.id 是普通 integer NOT NULL（无序列），用 max+1 显式取号
            cur.execute("SELECT coalesce(max(id),0)+1 FROM deal_followup")
            fid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO deal_followup (id, deal_id, stage_from, stage_to,
                                              note, operator, created_at, action_type, operator_id)
                   VALUES (%s,%s,%s,%s,%s,%s,now(),%s,%s)""",
                (fid, deal_id, "-", "-", note, operator_id or "agent", action_type, operator_id))
            fid = str(fid)
            conn.commit()
        return fid


class ChatService:
    """对话持久化。"""

    def ensure_conversation(self, conv_id: str, user_id: str, title: str | None = None) -> None:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM conversations WHERE conv_id=%s", (conv_id,))
            if cur.fetchone():
                cur.execute("UPDATE conversations SET last_active_at=now() WHERE conv_id=%s", (conv_id,))
            else:
                cur.execute(
                    "INSERT INTO conversations (conv_id, user_id, title) VALUES (%s,%s,%s)",
                    (conv_id, user_id, title or "新对话"))
            conn.commit()

    def add_message(self, conv_id: str, role: str, content: str,
                    tool_calls: dict | list | None = None, tool_call_id: str | None = None) -> str:
        mid = new_id("M")
        import json as _json
        with storage.pg_connect() as conn:
            conn.cursor().execute(
                """INSERT INTO messages (msg_id, conv_id, role, content, tool_calls, tool_call_id)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (mid, conv_id, role, content,
                 _json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                 tool_call_id))
            conn.commit()
        return mid

    def rename(self, conv_id: str, title: str) -> bool:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE conversations SET title=%s WHERE conv_id=%s", (title, conv_id))
            conn.commit()
            return cur.rowcount > 0

    def delete(self, conv_id: str) -> bool:
        """删会话；messages 无级联约束则显式删。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM conversations WHERE conv_id=%s", (conv_id,))
            if not cur.fetchone():
                return False
            cur.execute("DELETE FROM messages WHERE conv_id=%s", (conv_id,))
            cur.execute("DELETE FROM conversations WHERE conv_id=%s", (conv_id,))
            conn.commit()
            return True

    def maybe_autotitle(self, conv_id: str) -> None:
        """会话仍是默认标题时，用第一条用户消息提炼标题（截断即可，无需 LLM 成本）。"""
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT title FROM conversations WHERE conv_id=%s", (conv_id,))
            r = cur.fetchone()
            if not r or r[0] not in ("新对话", None, ""):
                return
            cur.execute("""SELECT content FROM messages WHERE conv_id=%s AND role='user'
                           ORDER BY created_at LIMIT 1""", (conv_id,))
            m = cur.fetchone()
            if not m:
                return
            first = (m[0] or "").strip().split("\n")[0]
            # 附件转写前缀等系统注入文本不作为标题
            if first.startswith("[图片附件") or not first:
                return
            title = first[:20] + ("…" if len(first) > 20 else "")
            cur.execute("UPDATE conversations SET title=%s WHERE conv_id=%s AND title IN ('新对话','')",
                        (title, conv_id))
            conn.commit()

    def history(self, conv_id: str, limit: int = 200) -> list[dict]:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT msg_id, role, content, tool_calls, tool_call_id, created_at
                           FROM messages WHERE conv_id=%s ORDER BY created_at LIMIT %s""",
                        (conv_id, limit))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
