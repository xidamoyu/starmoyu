"""端到端：钉住商单 → 对话内沉淀（LLM 不传 deal_id）→ 特质自动带 deal_id。"""
import json
import sys
import time

import httpx

sys.path.insert(0, "src")
from starmoyu import storage  # noqa: E402

c = httpx.Client(base_url="http://localhost:8000", timeout=300)
tok = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
h = {"Authorization": f"Bearer {tok}"}
conv = c.post("/api/conversations", headers=h, json={"title": "m6-e2e"}).json()["conv_id"]
c.patch(f"/api/conversations/{conv}/pin", headers=h, json={"deal_id": "DC20250011"})
t0 = int(time.time())


def send(text):
    buf, deal_events = [], []
    with c.stream("POST", f"/api/chat/{conv}", headers=h,
                  json={"text": text, "deal_id": "DC20250011"}) as r:
        cur_ev = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                cur_ev = line[7:]
            elif line.startswith("data: "):
                try:
                    d = json.loads(line[6:])
                except Exception:
                    continue
                if cur_ev == "deal_context":
                    deal_events.append(d)
                if cur_ev == "token" and d.get("text"):
                    buf.append(d["text"])
    return "".join(buf), deal_events


r1, ev1 = send("记录一下，昨天和「小美妆记」对接完了：必须提前一周给brief不然排期不下来。")
print("第一轮 deal_context 事件:", ev1[:1])
print("第一轮卡片含'确认':", "确认" in r1)
r2, _ = send("确认")
print("第二轮回复含 'DC20250011':", "DC20250011" in r2)

with storage.pg_connect() as conn:
    cur = conn.cursor()
    cur.execute("""SELECT trait_id, deal_id, trait_content FROM party_traits
                   WHERE source_type='chat' AND created_at > to_timestamp(%s)
                   ORDER BY created_at DESC""", (t0,))
    rows = cur.fetchall()
print("新落库特质:", len(rows))
for tr in rows:
    print("  deal_id:", tr[1], "|", (tr[2] or "")[:30])
ok = bool(rows) and all(tr[1] == "DC20250011" for tr in rows)
print("端到端断言:", "PASS" if ok else "FAIL")

# 清理
with storage.pg_connect() as conn:
    cur = conn.cursor()
    for tr in rows:
        cur.execute("DELETE FROM party_traits WHERE trait_id=%s", (tr[0],))
    cur.execute("DELETE FROM deal_followup WHERE deal_id='DC20250011' AND action_type='沉淀入库' AND created_at > to_timestamp(%s)", (t0,))
    cur.execute("DELETE FROM ingest_staging WHERE created_by='agent' AND suggested_kind='feedback' AND created_at > to_timestamp(%s)", (t0,))
    cur.execute("DELETE FROM messages WHERE conv_id=%s", (conv,))
    cur.execute("DELETE FROM conversations WHERE conv_id=%s", (conv,))
    cur.execute("DELETE FROM checkpoints WHERE thread_id LIKE %s", (conv + "%",)) if False else None
    conn.commit()
print("cleaned")
