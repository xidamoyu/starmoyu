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
from fastapi.exceptions import RequestValidationError
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


# 422 校验错误的 detail 是 [{loc, msg, type}...] 数组，前端 ElMessage 直接渲染会显示
# [object Object]（用户截图踩过）。统一转成可读字符串，前端任何端点都不再拿到对象/数组。
@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    parts = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
        parts.append(f"{loc}: {e.get('msg', '校验失败')}")
    raise HTTPException(status_code=422, detail="；".join(parts) or "请求参数校验失败")
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


def _jwt_payload(cred: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    if cred is None:
        raise HTTPException(401, "未登录")
    try:
        return jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(401, "凭证无效或已过期")


def _require_admin(payload: dict = Depends(_jwt_payload)) -> dict:
    """管理员专属操作守卫。"""
    if payload.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return payload


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
def me(payload: dict = Depends(_jwt_payload)):
    return {"user_id": payload["sub"], "role": payload.get("role", "user")}


# ============================================================ 用户管理（admin 专属）

class UserCreateBody(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "viewer"  # admin | operator | viewer（对应 users_role_check 约束）


class UserUpdateBody(BaseModel):
    display_name: str | None = None
    role: str | None = None
    password: str | None = None


@app.get("/api/admin/users")
def list_users(_admin: dict = Depends(_require_admin)):
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT user_id, username, display_name, role, created_at
                       FROM users ORDER BY created_at""")
        names = [d[0] for d in cur.description]
        return {"items": [dict(zip(names, r)) for r in cur.fetchall()]}


@app.post("/api/admin/users")
def create_user(body: UserCreateBody, admin: dict = Depends(_require_admin)):
    if body.role not in ("admin", "operator", "viewer"):
        raise HTTPException(422, "role 只能是 admin / operator / viewer")
    if len(body.password) < 6:
        raise HTTPException(422, "密码至少 6 位")
    user_id = f"u_{uuid.uuid4().hex[:12]}"
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE username=%s", (body.username,))
        if cur.fetchone():
            raise HTTPException(409, f"用户名 {body.username} 已存在")
        cur.execute(
            "INSERT INTO users (user_id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, body.username, bcrypt.hash(body.password),
             body.display_name or body.username, body.role))
    return {"ok": True, "user_id": user_id}


@app.patch("/api/admin/users/{user_id}")
def update_user(user_id: str, body: UserUpdateBody, admin: dict = Depends(_require_admin)):
    sets, args = [], []
    if body.display_name is not None:
        sets.append("display_name=%s"); args.append(body.display_name)
    if body.role is not None:
        if body.role not in ("admin", "operator", "viewer"):
            raise HTTPException(422, "role 只能是 admin / operator / viewer")
        if user_id == admin["sub"] and body.role != "admin":
            raise HTTPException(422, "不能降级自己的管理员角色")
        sets.append("role=%s"); args.append(body.role)
    if body.password is not None:
        if len(body.password) < 6:
            raise HTTPException(422, "密码至少 6 位")
        sets.append("password_hash=%s"); args.append(bcrypt.hash(body.password))
    if not sets:
        raise HTTPException(422, "没有要修改的字段")
    args.append(user_id)
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id=%s", args)
        if cur.rowcount == 0:
            raise HTTPException(404, "用户不存在")
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: str, admin: dict = Depends(_require_admin)):
    if user_id == admin["sub"]:
        raise HTTPException(422, "不能删除自己")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role FROM users WHERE user_id=%s", (user_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "用户不存在")
        cur.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    return {"ok": True}


# ============================================================ 对话

class ChatAttachment(BaseModel):
    name: str = "image.png"
    data_base64: str  # 纯 base64（不含 data: 前缀）


class ChatBody(BaseModel):
    text: str = ""
    title: str | None = None
    deal_id: str | None = None  # 会话钉住的商单（前端「关联」chip 传）
    attachments: list[ChatAttachment] = []  # 沉淀流图片输入（存 MinIO 留档）


@app.post("/api/conversations")
def new_conversation(body: ChatBody, sub: str = Depends(_jwt_sub)):
    conv_id = f"conv-{uuid.uuid4().hex[:12]}"
    cs.ensure_conversation(conv_id, sub, body.title)
    return {"conv_id": conv_id}


@app.get("/api/conversations")
def list_conversations(sub: str = Depends(_jwt_sub)):
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT conv_id, title, created_at, last_active_at, pinned_deal_id
                       FROM conversations WHERE user_id=%s
                       ORDER BY last_active_at DESC LIMIT 50""", (sub,))
        names = [d[0] for d in cur.description]
        return [{"row": dict(zip(names, r))} for r in cur.fetchall()]


class TitleBody(BaseModel):
    title: str


@app.patch("/api/conversations/{conv_id}/title")
def rename_conversation(conv_id: str, body: TitleBody, sub: str = Depends(_jwt_sub)):
    title = body.title.strip()
    if not title or len(title) > 60:
        raise HTTPException(422, "标题需为 1-60 字")
    if not cs.rename(conv_id, title):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str, sub: str = Depends(_jwt_sub)):
    if not cs.delete(conv_id):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@app.get("/api/conversations/{conv_id}/messages")
def get_messages(conv_id: str, sub: str = Depends(_jwt_sub)):
    return {"messages": cs.history(conv_id)}


@app.post("/api/chat/{conv_id}")
def chat(conv_id: str, body: ChatBody, sub: str = Depends(_jwt_sub)):
    """发送消息，SSE 流式返回 Agent 执行过程。"""
    agent = get_agent()
    cs.ensure_conversation(conv_id, sub, body.title)

    # 图片附件 → MinIO 留档 + qwen-vl 识图抽取文字（沉淀流图片输入路径）。
    # 注意 chat 主模型走纯文本通道，识图必须走 llm.chat_vision（qwen-vl）。
    attachment_notes: list[str] = []
    attachment_meta: list[dict] = []
    if body.attachments:
        import base64 as _b64
        import datetime as _dt
        from starmoyu import llm as _llm
        _imgs_ok: list[tuple[int, str, str]] = []  # (i, name, b64)
        for i, att in enumerate(body.attachments):
            try:
                raw = _b64.b64decode(att.data_base64)
                ext = ".png" if att.name.lower().endswith(".png") else (
                    ".jpg" if att.name.lower().endswith((".jpg", ".jpeg")) else ".bin")
                key = f"chat-attachments/{conv_id}/{_dt.datetime.now():%Y%m%d_%H%M%S}_{i}{ext}"
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tf:
                    tf.write(raw)
                    tmp = tf.name
                storage.upload_object(tmp, key)
                os.unlink(tmp)
                attachment_meta.append({"name": att.name, "object_key": key, "size": len(raw)})
                _imgs_ok.append((i, att.name, att.data_base64))
            except Exception:
                attachment_notes.append(f"[图片附件 {i + 1}: {att.name}，上传失败]")
        if _imgs_ok:
            try:
                _names = "、".join(n for _, n, _ in _imgs_ok)
                _desc = _llm.chat_vision(
                    "这是用户发来的聊天记录/业务截图。请完整转写图中的文字内容（对话逐条列出、"
                    "保留人名与数字），如有表格数据也原样转出。只输出转写内容，不要加评价。",
                    [b for _, _, b in _imgs_ok])
                attachment_notes.append(
                    f"[图片附件已识图（{_names}），转写内容如下——沉淀/录入时以这些内容为准：\n{_desc}\n"
                    f"图片原件已留档 {attachment_meta[0]['object_key']} 等]")
            except Exception as _ve:
                attachment_notes.append(
                    f"[图片附件 {_names} 已留档，但识图失败：{_ve}。请让用户文字补充关键信息，不要编造图片内容]")

    user_text = body.text or ""
    if attachment_notes:
        user_text = (user_text + "\n" if user_text else "") + \
            "\n".join(attachment_notes)
    cs.add_message(conv_id, "user", user_text)
    cs.maybe_autotitle(conv_id)   # 默认标题时以首问提炼会话名

    # 会话钉住的商单（用户点「关联」时前端会持续传 deal_id；取库中现值兜底）
    from server import m6_service as m6
    if body.deal_id:
        try:
            m6.pin_deal(conv_id, body.deal_id, sub)
        except ValueError:
            pass  # 商单号无效时忽略，不阻断对话
    pinned = m6.get_pinned(conv_id) if not body.deal_id else body.deal_id

    def gen():
        final_text_parts: list[str] = []
        seen_deal_ids: set[str] = set()
        try:
            # 开场先推钉住状态（前端渲染关联 chip / 提示条）
            yield f"event: deal_context\ndata: {json.dumps({'pinned_deal_id': pinned}, ensure_ascii=False)}\n\n"
            if attachment_meta:
                yield (f"event: attachments\ndata: "
                       f"{json.dumps({'attachments': attachment_meta}, ensure_ascii=False)}\n\n")
            # 用户输入里直接提到的商单号也弹关联提示（此前只扫工具结果，
            # 首轮确认卡/结案粘贴不调工具 → 商单号检测不到）
            for did in m6.extract_deal_ids(body.text or ""):
                if did != pinned:
                    seen_deal_ids.add(did)
                    yield f"event: deal_context\ndata: {json.dumps({'pinned_deal_id': pinned, 'detected_deal_id': did}, ensure_ascii=False)}\n\n"
            for ev in agent.chat_stream(conv_id, user_text, pinned_deal_id=pinned):
                if ev["type"] == "token":
                    final_text_parts.append(ev.get("text") or "")
                if ev["type"] == "tool_result":
                    # 确定性提取商单号 → 推给前端弹「关联」提示
                    for did in m6.extract_deal_ids(ev.get("preview") or ""):
                        if did not in seen_deal_ids and did != pinned:
                            seen_deal_ids.add(did)
                            yield f"event: deal_context\ndata: {json.dumps({'pinned_deal_id': pinned, 'detected_deal_id': did}, ensure_ascii=False)}\n\n"
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


class DealChangeCreateBody(BaseModel):
    deal_id: str
    field_name: str
    new_value: str
    change_summary: str = ""
    conv_id: str = ""


class DealChangeReviewBody(BaseModel):
    comment: str = ""


@app.post("/api/proposals/{proposal_id}/review")
def review_proposal(proposal_id: str, body: ReviewBody, sub: str = Depends(_jwt_sub)):
    try:
        ProposalService().transition(proposal_id, body.action, body.comment,
                                     changed_by=sub)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "status": ProposalService().get(proposal_id)["status"]}


# ============================================================ M7b：商单字段变更审批
# 对话内 Agent 识别商单变更 → request_deal_change 工具 → 本端点审批 → 批准自动改 deal

@app.post("/api/deal-changes")
def create_deal_change(body: DealChangeCreateBody, sub: str = Depends(_jwt_sub)):
    from server import m7b_service as m7b
    try:
        return m7b.DealChangeService().create(
            deal_id=body.deal_id, field_name=body.field_name,
            new_value=body.new_value, change_summary=body.change_summary,
            conv_id=body.conv_id, created_by=sub)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/deal-changes")
def list_deal_changes(sub: str = Depends(_jwt_sub), status: str = "",
                      deal_id: str = "", limit: int = 100):
    from server import m7b_service as m7b
    return {"items": m7b.DealChangeService().list(status=status, deal_id=deal_id, limit=limit)}


@app.get("/api/deal-changes/{request_id}")
def get_deal_change(request_id: str, sub: str = Depends(_jwt_sub)):
    from server import m7b_service as m7b
    r = m7b.DealChangeService().get(request_id)
    if not r:
        raise HTTPException(404, "变更申请不存在")
    return r


@app.post("/api/deal-changes/{request_id}/approve")
def approve_deal_change(request_id: str, body: DealChangeReviewBody, sub: str = Depends(_jwt_sub)):
    from server import m7b_service as m7b
    try:
        return m7b.DealChangeService().approve(request_id, sub, body.comment)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/deal-changes/{request_id}/reject")
def reject_deal_change(request_id: str, body: DealChangeReviewBody, sub: str = Depends(_jwt_sub)):
    from server import m7b_service as m7b
    try:
        return m7b.DealChangeService().reject(request_id, sub, body.comment)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ============================================================ M2：达人库（含档期/排他）

KOL_COLS = ("kol_id", "kol_name", "platform", "category", "sub_category",
            "tier", "fans_count", "interact_rate", "price_21_60s", "avg_views",
            "exclusive_until", "available_from", "blacklist")


@app.get("/api/kols")
def list_kols(sub: str = Depends(_jwt_sub), q: str = "", category: str = "",
              tier: str = "", page: int = 1, page_size: int = 20,
              sort: str = "fans"):
    """达人库分页列表（企业表格口径：总数/分页/统计条/类目 facets）。"""
    where, args = ["1=1"], []
    if q:
        where.append("kol_name ILIKE %s")
        args.append(f"%{q}%")
    if category:
        where.append("category=%s")
        args.append(category)
    if tier:
        where.append("tier=%s")
        args.append(tier)
    w = " AND ".join(where)
    order = {"fans": "fans_count DESC", "price": "price_21_60s DESC",
             "interact": "interact_rate DESC NULLS LAST"}.get(sort, "fans_count DESC")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM kol_profile WHERE {w}", args)
        total = cur.fetchone()[0]
        # 统计条（按当前筛选口径）：总粉丝/均价/排期冲突数/黑名单数
        cur.execute(f"""SELECT coalesce(sum(fans_count),0),
                               coalesce(avg(price_21_60s),0),
                               count(*) FILTER (WHERE exclusive_until IS NOT NULL),
                               count(*) FILTER (WHERE blacklist IS NOT NULL)
                        FROM kol_profile WHERE {w}""", args)
        sum_fans, avg_price, n_excl, n_black = cur.fetchone()
        # 类目 facets（不筛类目时返回，供下拉）
        facets: list[dict] = []
        if not category:
            cur.execute("""SELECT category, count(*) FROM kol_profile
                           WHERE {w} GROUP BY category ORDER BY count(*) DESC""".format(w=w),
                        args)
            facets = [{"category": r[0], "count": r[1]} for r in cur.fetchall()]
        page = max(page, 1)
        page_size = min(max(page_size, 10), 100)
        cur.execute(f"""SELECT kol_id, kol_name, platform, category, sub_category, tier,
                               fans_count, interact_rate, price_21_60s, avg_views,
                               exclusive_until, available_from, blacklist
                        FROM kol_profile WHERE {w}
                        ORDER BY {order} LIMIT %s OFFSET %s""",
                    args + [page_size, (page - 1) * page_size])
        names = [d[0] for d in cur.description]
        return {"items": [dict(zip(names, r)) for r in cur.fetchall()],
                "total": total, "page": page, "page_size": page_size,
                "stats": {"sum_fans": int(sum_fans), "avg_price": round(float(avg_price)),
                          "exclusive_count": n_excl, "blacklist_count": n_black},
                "facets": facets}


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
    sql = """SELECT d.deal_id, d.brand_name, d.category, d.sub_category,
                    d.budget, d.goal, d.stage, d.demand_desc, d.start_date, d.end_date,
                    d.created_at
             FROM deal d WHERE 1=1"""
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
    import io
    if filename.lower().endswith((".xlsx", ".xls")):
        from openpyxl import load_workbook
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
    """批量导入。kols 直导（低风险，ON CONFLICT 兜底）；deals/docs 先入暂存区
    （Agent 预审 + 管理员确认双关后才落正式表/向量库）。"""
    if sub not in ("admin", "u-admin"):
        raise HTTPException(403, "仅管理员可导入")
    data = await file.read()
    fname = file.filename or ""
    if kind == "docs":
        # 非结构化文档：txt/md/csv 原文整篇入暂存，等双审后向量化入库
        from server.m4_service import IngestService
        raw = data.decode("utf-8-sig", errors="replace")
        if not raw.strip():
            raise HTTPException(422, "文件内容为空")
        svc = IngestService()
        sid = svc.stage("import_doc", "admin_import", raw,
                        extracted={"title": fname, "size": len(raw)},
                        created_by=sub)
        return {"ok": True, "staged": 1, "staging_ids": [sid],
                "hint": "已入暂存区，先 Agent 预审再管理员确认后才会进知识库"}
    try:
        rows = _parse_rows(data, fname)
    except Exception as e:
        raise HTTPException(422, f"解析失败: {e}")
    if not rows:
        raise HTTPException(422, "未解析到任何数据行")

    if kind == "deals":
        # 商单批量导入：逐行入暂存（不直接写 deal 表），Agent 预审 + 管理员确认
        from server.m4_service import IngestService
        svc = IngestService()
        ids: list[str] = []
        for r in rows:
            if not (r.get("brand_name") or r.get("品牌")):
                continue
            ex = {k: (v if v not in ("", None) else None)
                  for k, v in r.items() if k in (
                      "deal_id", "brand_name", "category", "sub_category", "goal",
                      "budget", "stage", "demand_desc", "start_date", "end_date",
                      "owner", "note")}
            ids.append(svc.stage("import_deal", "admin_import",
                                 json.dumps(r, ensure_ascii=False, default=str),
                                 extracted=ex, created_by=sub))
        return {"ok": True, "rows_parsed": len(rows), "staged": len(ids),
                "staging_ids": ids[:50],
                "hint": f"{len(ids)} 行已入暂存区，请在下方暂存区完成 Agent 预审 + 确认"}

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


@app.get("/api/admin/staging")
def list_staging(status: str = "pending", sub: str = Depends(_jwt_sub)):
    """暂存导入队列（管理员）。"""
    from server.m4_service import IngestService
    limit = 100
    items = IngestService().pending(limit=limit)
    if status != "pending":
        # 非 pending 状态直接查表
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT staging_id, suggested_kind, source_type,
                                  LEFT(raw_content, 80) AS raw_preview, status,
                                  reviewed_by, created_at
                           FROM ingest_staging WHERE status=%s
                           ORDER BY created_at DESC LIMIT %s""", (status, limit))
            names = [d[0] for d in cur.description]
            items = [dict(zip(names, r)) for r in cur.fetchall()]
    return {"items": items}


class ReviewBody(BaseModel):
    staging_id: str


@app.post("/api/admin/staging/agent-review")
def agent_review_staging(body: ReviewBody, sub: str = Depends(_jwt_sub)):
    """Agent 预审一条暂存导入（LLM 质检），结果写回 extracted.agent_review。"""
    from server.m4_service import IngestService
    try:
        return IngestService().agent_review(body.staging_id, reviewer=sub)
    except ValueError as e:
        raise HTTPException(422, str(e))


class ConfirmBody(BaseModel):
    staging_id: str
    final_fields: dict = {}


@app.post("/api/admin/staging/confirm")
def confirm_staging(body: ConfirmBody, sub: str = Depends(_jwt_sub)):
    """管理员确认暂存导入 → 落正式表/向量库（Agent 预审意见仅供参照）。"""
    from server.m4_service import IngestService
    try:
        r = IngestService().confirm(body.staging_id, body.final_fields, reviewer=sub)
        # import_doc 确认后向量入库
        if r.get("kind") == "import_doc" and r.get("vectorize_text"):
            pass  # confirm 内部(import_doc 分支)已完成向量入库
        return r
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/admin/staging/reject")
def reject_staging(body: ReviewBody, sub: str = Depends(_jwt_sub)):
    from server.m4_service import IngestService
    if IngestService().reject(body.staging_id, reviewer=sub):
        return {"ok": True}
    raise HTTPException(422, "驳回失败（可能已处理）")


# ============================================================ M6: 商单钉住/路线图/结案表单

class PinBody(BaseModel):
    deal_id: str | None = None  # None = 取消钉住


class StageBody(BaseModel):
    stage: str
    note: str = ""


class TraitSpec(BaseModel):
    kol_id: str
    trait_category: str = "其他"
    trait_content: str
    source_quote: str = ""
    severity: str = "info"


class CloseDealBody(BaseModel):
    roi: float | None = None
    gmv: float | None = None
    exposure: float | None = None
    interaction: float | None = None
    cpm: float | None = None
    views: float | None = None
    summary_note: str = ""
    traits: list[TraitSpec] = []


@app.patch("/api/conversations/{conv_id}/pin")
def pin_conversation_deal(conv_id: str, body: PinBody, sub: str = Depends(_jwt_sub)):
    """钉住/取消钉住商单到会话。"""
    from server import m6_service as m6
    try:
        return m6.pin_deal(conv_id, body.deal_id, sub)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/conversations/{conv_id}/pin")
def get_conversation_pin(conv_id: str, sub: str = Depends(_jwt_sub)):
    """读取会话当前钉住的商单。"""
    from server import m6_service as m6
    return {"conv_id": conv_id, "pinned_deal_id": m6.get_pinned(conv_id)}


@app.get("/api/deals/{deal_id}/timeline")
def deal_timeline(deal_id: str, sub: str = Depends(_jwt_sub)):
    """商单路线图：stage 时间线 + 跟进流水 + 特质溯源。"""
    from server import m6_service as m6
    d = m6.deal_timeline(deal_id)
    if not d:
        raise HTTPException(404, f"商单不存在: {deal_id}")
    return d


@app.get("/api/vision/enabled")
def vision_enabled():
    """视觉（截图→特质）抽取能力是否可用。

    探测结论：chat 主模型（纯文本通道）对真实截图内容识别不可靠（自报无法加载图片），
    故置灰。前端据此隐藏抽取 UI、仅把截图作附件留档。接入可靠视觉模型后置 True 即启用。
    """
    return {"enabled": False,
            "reason": "当前模型视觉能力未验证可用；接入视觉模型后启用截图自动抽取。"}


@app.post("/api/deals/{deal_id}/stage")
def set_deal_stage(deal_id: str, body: StageBody, sub: str = Depends(_jwt_sub)):
    """修改商单阶段（写流水留痕）。"""
    from server import m6_service as m6
    try:
        return m6.set_stage(deal_id, body.stage, sub, body.note)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/deals/{deal_id}/close")
def close_deal(deal_id: str, body: CloseDealBody, sub: str = Depends(_jwt_sub)):
    """结案表单直调（UI 确定性路径，人工逐字段填写，不经 LLM 确认卡）。"""
    from server import m6_service as m6
    metrics = {k: v for k, v in {
        "roi": body.roi, "gmv": body.gmv, "exposure": body.exposure,
        "interaction": body.interaction, "cpm": body.cpm, "views": body.views,
    }.items() if v is not None}
    if not metrics:
        raise HTTPException(422, "至少填写一个效果指标")
    try:
        return m6.close_deal_form(deal_id, metrics, body.summary_note,
                                  [t.model_dump() for t in body.traits], operator=sub)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ---------------------------------------------------------------- 沉淀流（P1+P2）

class SedimentBody(BaseModel):
    extra_lessons: list[str] = []   # 用户在确认卡补充的经验要点


@app.post("/api/deals/{deal_id}/sediment/preview")
def sediment_preview(deal_id: str, body: SedimentBody, sub: str = Depends(_jwt_sub)):
    """结案沉淀第一步：渲染案例文档草稿（含流水提炼的复盘经验），供确认卡预览。"""
    from server.sediment_service import sediment_closed_deal
    try:
        return sediment_closed_deal(deal_id, body.extra_lessons, created_by=sub)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/deals/{deal_id}/sediment/confirm")
def sediment_confirm(deal_id: str, body: SedimentBody, sub: str = Depends(_jwt_sub)):
    """结案沉淀第二步：确认卡批准 → 增量切块嵌入 → 入 PG chunk 表 + Milvus。"""
    from server.sediment_service import confirm_sediment
    try:
        return confirm_sediment(deal_id, body.extra_lessons, confirmed_by=sub)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/traits/{trait_id}/confirm")
def confirm_trait(trait_id: str, sub: str = Depends(_jwt_sub)):
    """确认画像（verified=TRUE）——沉淀流 P3 的人工关口。"""
    from server.m4_service import TraitService
    ok = TraitService().confirm(trait_id, reviewer=sub)
    if not ok:
        raise HTTPException(404, f"画像不存在: {trait_id}")
    return {"ok": True, "trait_id": trait_id, "verified": True}


@app.get("/api/traits/pending")
def pending_traits(sub: str = Depends(_jwt_sub)):
    """待确认画像列表。"""
    from server.m4_service import TraitService
    return TraitService().pending_review(limit=50)


@app.get("/api/sediment/stats")
def sediment_stats(sub: str = Depends(_jwt_sub)):
    """沉淀流看板：各通道的累积量（体现 RAG 随业务运转生长）。"""
    from server.sediment_service import _q
    n_deal_case = _q("SELECT count(*) AS n FROM chunk_meta WHERE doc_type='deal_case'")[0]["n"]
    n_closed = _q("SELECT count(*) AS n FROM deal WHERE stage='结案'")[0]["n"]
    n_traits = _q("SELECT count(*) AS n FROM party_traits WHERE verified=TRUE")[0]["n"]
    n_followup = _q("SELECT count(*) AS n FROM deal_followup")[0]["n"]
    return {"chunks_by_type": {"deal_case": n_deal_case},
            "deals_closed": n_closed, "traits_verified": n_traits,
            "followups": n_followup}
