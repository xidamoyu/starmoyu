"""FastAPI 后端：认证 + 对话(SSE) + 业务 CRUD（M1）。

启动：
  cd C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu
  export PYTHONPATH="$PWD/src"
  .venv/Scripts/python.exe -m uvicorn server.api:app --port 8000
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.hash import bcrypt
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from server.service import ChatService, FollowupService, ProposalService  # noqa: E402
from starmoyu import storage  # noqa: E402

SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_EXP_H = 24

app = FastAPI(title="MCN 商单助手 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vue dev server
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

cs, ps, fs = ChatService(), ProposalService(), FollowupService()
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        from agent_graph import MCAgent
        _agent = MCAgent()
    return _agent


security = HTTPBearer(auto_error=False)


def _jwt_sub(cred: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if cred is None:
        raise HTTPException(401, "未登录")
    try:
        payload = jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])
        return payload["sub"]
    except JWTError:
        raise HTTPException(401, "凭证无效或已过期")


# ============================================================ 认证

class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginBody):
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, password_hash, role FROM users WHERE username=%s",
                    (body.username,))
        r = cur.fetchone()
    if not r or not bcrypt.verify(body.password, r[1]):
        raise HTTPException(401, "用户名或密码错误")
    token = jwt.encode({"sub": r[0], "role": r[2],
                        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXP_H)},
                       SECRET, algorithm="HS256")
    return {"token": token, "user_id": r[0], "role": r[2]}


@app.get("/api/me")
def me(sub: str = Depends(_jwt_sub)):
    return {"user_id": sub}


# ============================================================ 对话

class ChatBody(BaseModel):
    text: str = ""
    title: str | None = None


@app.post("/api/conversations")
def new_conversation(body: ChatBody, sub: str = Depends(_jwt_sub)):
    conv_id = f"conv-{uuid.uuid4().hex[:12]}"
    cs.ensure_conversation(conv_id, sub, body.title)
    return {"conv_id": conv_id}


@app.get("/api/conversations")
def list_conversations(sub: str = Depends(_jwt_sub)):
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT conv_id, title, created_at, last_active_at
                       FROM conversations WHERE user_id=%s
                       ORDER BY last_active_at DESC LIMIT 50""", (sub,))
        names = [d[0] for d in cur.description]
        return [{"row": dict(zip(names, r))} for r in cur.fetchall()]


@app.get("/api/conversations/{conv_id}/messages")
def get_messages(conv_id: str, sub: str = Depends(_jwt_sub)):
    return {"messages": cs.history(conv_id)}


@app.post("/api/chat/{conv_id}")
def chat(conv_id: str, body: ChatBody, sub: str = Depends(_jwt_sub)):
    """发送消息，SSE 流式返回 Agent 执行过程。"""
    agent = get_agent()
    cs.ensure_conversation(conv_id, sub, body.title)
    cs.add_message(conv_id, "user", body.text)

    def gen():
        final_text_parts: list[str] = []
        try:
            for ev in agent.chat_stream(conv_id, body.text):
                if ev["type"] == "token":
                    final_text_parts.append(ev.get("text") or "")
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            cs.add_message(conv_id, "assistant", "".join(final_text_parts))
        except Exception as e:
            msg = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
            yield f"event: error\ndata: {msg}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ============================================================ 业务（M1 最小集）

@app.get("/api/deals/{deal_id}/followups")
def deal_followups(deal_id: str, sub: str = Depends(_jwt_sub)):
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT id, note, action_type, operator, created_at
                       FROM deal_followup WHERE deal_id=%s ORDER BY created_at DESC LIMIT 50""",
                    (deal_id,))
        names = [d[0] for d in cur.description]
        return {"n": cur.rowcount, "items": [dict(zip(names, r)) for r in cur.fetchall()]}


@app.get("/api/proposals")
def list_proposals(sub: str = Depends(_jwt_sub)):
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT proposal_id, requirement_text, status, created_at, updated_at
                       FROM proposals ORDER BY created_at DESC LIMIT 50""")
        names = [d[0] for d in cur.description]
        return {"items": [dict(zip(names, r)) for r in cur.fetchall()]}


@app.get("/api/health")
def health():
    try:
        h = storage.health()
        return {"ok": True, "infra": h, "agent": "ready" if _agent is not None else "lazy"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
