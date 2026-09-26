"""FastAPI 后端：认证 + 对话(SSE) + 业务 CRUD（M1）。

启动：
  cd C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu
  export PYTHONPATH="$PWD/src"
  .venv/Scripts/python.exe -m uvicorn server.api:app --port 8000
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
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


# ============================================================ M2：方案全生命周期

@app.get("/api/proposals/{proposal_id}")
def proposal_detail(proposal_id: str, sub: str = Depends(_jwt_sub)):
    p = ProposalService().get(proposal_id)
    if not p:
        raise HTTPException(404, "方案不存在")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT version_id, version_no, content, comment, changed_by, created_at
                       FROM proposal_versions WHERE proposal_id=%s ORDER BY version_no""",
                    (proposal_id,))
        names = [d[0] for d in cur.description]
        p["versions"] = [dict(zip(names, r)) for r in cur.fetchall()]
    return p


class ReviewBody(BaseModel):
    action: str
    comment: str = ""


@app.post("/api/proposals/{proposal_id}/review")
def review_proposal(proposal_id: str, body: ReviewBody, sub: str = Depends(_jwt_sub)):
    try:
        ProposalService().transition(proposal_id, body.action, body.comment,
                                     changed_by=sub)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "status": ProposalService().get(proposal_id)["status"]}


# ============================================================ M2：达人库（含档期/排他）

KOL_COLS = ("kol_id", "kol_name", "platform", "category", "sub_category",
            "tier", "fans_count", "interact_rate", "price_21_60s", "avg_views",
            "exclusive_until", "available_from", "blacklist")


@app.get("/api/kols")
def list_kols(sub: str = Depends(_jwt_sub), q: str = "", category: str = "",
              tier: str = "", limit: int = 50):
    sql = """SELECT kol_id, kol_name, platform, category, sub_category, tier,
                    fans_count, interact_rate, price_21_60s, avg_views,
                    exclusive_until, available_from, blacklist
             FROM kol_profile WHERE 1=1"""
    args: list = []
    if q:
        sql += " AND kol_name ILIKE %s"
        args.append(f"%{q}%")
    if category:
        sql += " AND category=%s"
        args.append(category)
    if tier:
        sql += " AND tier=%s"
        args.append(tier)
    sql += " ORDER BY fans_count DESC LIMIT %s"
    args.append(min(limit, 200))
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, args)
        names = [d[0] for d in cur.description]
        return {"items": [dict(zip(names, r)) for r in cur.fetchall()]}


class KolPatch(BaseModel):
    exclusive_until: str | None = None
    available_from: str | None = None
    blacklist: str | None = None


@app.patch("/api/kols/{kol_id}")
def patch_kol(kol_id: str, body: KolPatch, sub: str = Depends(_jwt_sub)):
    sets, args = [], []
    for f in ("exclusive_until", "available_from", "blacklist"):
        v = getattr(body, f)
        if v is not None:
            sets.append(f"{f}=%s")
            args.append(v if v != "" else None)
    if not sets:
        raise HTTPException(422, "无可更新字段")
    args.append(kol_id)
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE kol_profile SET {', '.join(sets)} WHERE kol_id=%s", args)
        n = cur.rowcount
        conn.commit()
    if not n:
        raise HTTPException(404, "达人不存在")
    return {"ok": True, "updated": n}


# ============================================================ M2：商单台账

@app.get("/api/deals")
def list_deals(sub: str = Depends(_jwt_sub), status: str = "", limit: int = 50):
    sql = """SELECT d.deal_id, d.brand_id, b.brand_name, d.product, d.category,
                    d.amount, d.stage, d.risk_level, d.created_at
             FROM deal d LEFT JOIN brand b ON d.brand_id=b.brand_id WHERE 1=1"""
    args: list = []
    if status:
        sql += " AND d.stage=%s"
        args.append(status)
    sql += " ORDER BY d.created_at DESC LIMIT %s"
    args.append(min(limit, 200))
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, args)
        names = [d[0] for d in cur.description]
        return {"items": [dict(zip(names, r)) for r in cur.fetchall()]}


# ============================================================ M2：Excel/CSV 导入

def _parse_rows(b: bytes, filename: str) -> list[dict]:
    """xlsx 用 openpyxl，csv 直接解。返回 dict 行。"""
    if filename.lower().endswith((".xlsx", ".xls")):
        from openpyxl import load_workbook
        import io
        wb = load_workbook(io.BytesIO(b), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        head = [str(h).strip() for h in rows[0]]
        return [dict(zip(head, r)) for r in rows[1:] if any(v is not None for v in r)]
    text = b.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


@app.post("/api/admin/import")
async def admin_import(file: UploadFile, kind: str = "kols",
                       sub: str = Depends(_jwt_sub)):
    if sub not in ("admin", "u-admin"):
        raise HTTPException(403, "仅管理员可导入")
    data = await file.read()
    try:
        rows = _parse_rows(data, file.filename or "")
    except Exception as e:
        raise HTTPException(422, f"解析失败: {e}")

    inserted = 0
    if kind == "kols":
        cols = KOL_COLS
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM kol_profile")
            before = cur.fetchone()[0]
            for r in rows:
                nick = r.get("kol_name") or r.get("nickname")
                if not nick:
                    continue
                r = {**r, "kol_name": nick}
                kid = r.get("kol_id") or f"KOL-{uuid.uuid4().hex[:10]}"
                vals = [r.get(c) for c in cols[1:]]
                cur.execute(
                    """INSERT INTO kol_profile
                       (kol_id, kol_name, platform, category, sub_category, tier,
                        fans_count, interact_rate, price_21_60s, avg_views,
                        exclusive_until, available_from, blacklist)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (kol_id) DO NOTHING""",
                    (kid, *vals))
                inserted += 1
            conn.commit()
            cur.execute("SELECT count(*) FROM kol_profile")
            after = cur.fetchone()[0]
            inserted = after - before  # 以实际落库数为准（冲突跳过）
    else:
        raise HTTPException(422, f"暂不支持 kind={kind}")
    return {"ok": True, "rows_parsed": len(rows), "inserted": inserted}
