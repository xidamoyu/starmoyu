"""入库管线：Markdown 语料 → 元数据打标 → 父子块切分 → 本地向量化 → 三库写入。

写入目标：
  Milvus     —— 子块向量 + 标量元数据（HNSW + 倒排索引）
  PostgreSQL —— 达人/品牌/商单/跟进 关系型数据 + 父子块文本
  MinIO      —— 原始 Markdown 文件（模拟真实原件，支持预签名直链）
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import llm, storage  # noqa: E402

RAW = ROOT / "data" / "raw"
SYN = ROOT / "data" / "synthetic"

MAX_CHARS = 560
OVERLAP = 80

DOC_TYPE_MAP = {
    "policies": "platform_policy",
    "playbook": "methodology",
    "rate_cards": "rate_card",
    "deal_cases": "deal_case",
}

PG_DDL = """
CREATE TABLE IF NOT EXISTS kol_profile (
    kol_id TEXT PRIMARY KEY, kol_name TEXT, real_name TEXT, platform TEXT,
    category TEXT, sub_category TEXT, fans_count INT, tier TEXT, avg_views INT,
    interact_rate REAL, conversion_rate REAL, gender_ratio JSONB, age_ratio JSONB,
    region TEXT, mcn_name TEXT, cooperation_level TEXT, tags JSONB,
    price_1_20s INT, price_21_60s INT, price_live INT
);
CREATE INDEX IF NOT EXISTS idx_kol_cat ON kol_profile(category, fans_count);
CREATE INDEX IF NOT EXISTS idx_kol_tier ON kol_profile(tier);

CREATE TABLE IF NOT EXISTS brand (
    brand_id TEXT PRIMARY KEY, brand_name TEXT, industry TEXT, category TEXT,
    sub_category TEXT, cooperation_count INT, history_budget_total NUMERIC(14,2)
);

CREATE TABLE IF NOT EXISTS deal (
    deal_id TEXT PRIMARY KEY, brand_id TEXT, brand_name TEXT, category TEXT,
    sub_category TEXT, goal TEXT, budget NUMERIC(12,2), stage TEXT, demand_desc TEXT,
    kol_ids JSONB, start_date DATE, end_date DATE, owner TEXT,
    result_metrics JSONB, created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_deal_stage ON deal(stage);

CREATE TABLE IF NOT EXISTS deal_followup (
    id INT PRIMARY KEY, deal_id TEXT, stage_from TEXT, stage_to TEXT,
    note TEXT, operator TEXT, created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS parent_chunk (
    parent_id TEXT PRIMARY KEY, doc_id TEXT, doc_title TEXT, section TEXT, content TEXT
);

CREATE TABLE IF NOT EXISTS chunk_meta (
    chunk_id TEXT PRIMARY KEY, parent_id TEXT, doc_id TEXT, doc_title TEXT,
    doc_type TEXT, source_file TEXT, object_key TEXT, section TEXT, chunk_type TEXT,
    category TEXT, platform TEXT, tier TEXT, deal_year INT, char_len INT, content TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk_meta(doc_id);
"""


# ---------------------------------------------------------------- Markdown 解析

def parse_markdown(text: str) -> tuple[str, list[tuple[str, str]]]:
    lines = text.split("\n")
    title = ""
    sections: list[tuple[str, list[str]]] = []
    cur_head, cur_body = "", []
    for ln in lines:
        m = re.match(r"^(#{1,3})\s+(.*)$", ln)
        if m:
            level, head = len(m.group(1)), m.group(2).strip()
            if level == 1 and not title:
                title = head
                continue
            if cur_head or cur_body:
                sections.append((cur_head, cur_body))
            cur_head, cur_body = head, []
        else:
            cur_body.append(ln)
    if cur_head or cur_body:
        sections.append((cur_head, cur_body))
    return title, [(h, "\n".join(b).strip()) for h, b in sections if "\n".join(b).strip()]


def split_section(body: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    units: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            units.append(p)
        else:
            lines, buf = p.split("\n"), ""
            for ln in lines:
                if len(buf) + len(ln) + 1 > max_chars and buf:
                    units.append(buf)
                    buf = ln
                else:
                    buf = f"{buf}\n{ln}" if buf else ln
            if buf:
                units.append(buf)
    chunks, buf = [], ""
    for u in units:
        if len(buf) + len(u) + 2 > max_chars and buf:
            chunks.append(buf)
            tail = buf[-overlap:] if len(buf) > overlap else buf
            buf = tail + "\n" + u
        else:
            buf = f"{buf}\n\n{u}" if buf else u
    if buf:
        chunks.append(buf)
    return chunks


def extract_meta(rel: str, title: str, body: str) -> dict:
    parts = Path(rel).parts
    doc_type = DOC_TYPE_MAP.get(parts[0], "other")
    if parts[0] == "deal_cases" and len(parts) > 2:
        doc_type = "inflight_deal"
    meta = {"doc_type": doc_type, "category": None, "platform": None,
            "tier": None, "deal_year": None}
    if doc_type == "rate_card":
        meta["category"] = title.split("类目")[0].replace("#", "").strip()
    if doc_type in ("deal_case", "inflight_deal"):
        m = re.search(r"类目：([^\s/]+)", body)
        if m:
            meta["category"] = m.group(1)
        for pf in ("抖音", "小红书", "快手"):
            if pf in body:
                meta["platform"] = pf
                break
        tiers = [t for t in ("头部", "腰部", "尾部") if t in body]
        if tiers:
            meta["tier"] = "/".join(tiers)
        m = re.search(r"商单编号：DC(\d{4})", body) or re.search(r"执行周期：(\d{4})", body)
        if m:
            meta["deal_year"] = int(m.group(1))
    return meta


def classify_chunk(section: str, content: str) -> str:
    s = section + content[:60]
    if any(k in s for k in ("刊例", "报价", "价格")):
        return "pricing"
    if any(k in s for k in ("结案", "复盘", "效果数据")):
        return "review"
    if any(k in s for k in ("需求", "客户需求")):
        return "requirement"
    if any(k in s for k in ("达人组合", "执行方案", "候选达人")):
        return "kol_plan"
    if any(k in s for k in ("规则", "政策", "流程", "SOP", "合规")):
        return "rule"
    if any(k in s for k in ("方法论", "框架", "策略", "要点", "话术")):
        return "method"
    return "general"


def build_chunks() -> tuple[list[dict], list[dict]]:
    parents, chunks = [], []
    for path in sorted(RAW.rglob("*.md")):
        rel = path.relative_to(RAW).as_posix()
        text = path.read_text(encoding="utf-8")
        title, sections = parse_markdown(text)
        doc_id = rel.replace("/", "__").replace(".md", "")
        meta = extract_meta(rel, title, text)
        object_key = f"docs/{rel}"
        for si, (sec_title, sec_body) in enumerate(sections):
            parent_id = f"{doc_id}#s{si}"
            parents.append({"parent_id": parent_id, "doc_id": doc_id, "doc_title": title,
                            "section": sec_title, "content": f"{sec_title}\n{sec_body}"})
            for ci, ck in enumerate(split_section(sec_body)):
                content = f"【{title}】{sec_title}\n{ck}" if sec_title else f"【{title}】\n{ck}"
                chunks.append({
                    "chunk_id": f"{parent_id}#c{ci}", "parent_id": parent_id, "doc_id": doc_id,
                    "doc_title": title, "doc_type": meta["doc_type"], "source_file": rel,
                    "object_key": object_key, "section": sec_title,
                    "chunk_type": classify_chunk(sec_title, ck),
                    "category": meta["category"] or "", "platform": meta["platform"] or "",
                    "tier": meta["tier"] or "", "deal_year": meta["deal_year"] or 0,
                    "char_len": len(content), "content": content,
                })
    return parents, chunks


# ---------------------------------------------------------------- 主流程

def main() -> None:
    print("=" * 88)
    print("Starmoyu 入库：Milvus(向量) + PostgreSQL(关系) + MinIO(原件)")
    print("=" * 88)

    h = storage.health()
    for k, v in h.items():
        print(f"  {'OK ' if v['ok'] else 'ERR'} {k}: {v.get('error') or v}")
    if not all(v["ok"] for v in h.values()):
        print("存储组件不可用，终止。")
        return

    print("\n[1] 写入 PostgreSQL 关系数据")
    db = storage.pg_ensure_database()
    con = storage.pg_connect(db)
    for stmt in [s.strip() for s in PG_DDL.split(";") if s.strip()]:
        con.execute(stmt)

    kols = json.loads((SYN / "kol_profile.json").read_text(encoding="utf-8"))
    with con.cursor() as cur:
        cur.execute("TRUNCATE kol_profile")
        cur.executemany(
            "INSERT INTO kol_profile VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(k["kol_id"], k["kol_name"], k["real_name"], k["platform"], k["category"],
              k["sub_category"], k["fans_count"], k["tier"], k["avg_views"], k["interact_rate"],
              k["conversion_rate"], json.dumps(k["gender_ratio"]), json.dumps(k["age_ratio"]),
              k["region"], k["mcn_name"], k["cooperation_level"], json.dumps(k["tags"]),
              k["price_1_20s"], k["price_21_60s"], k["price_live"]) for k in kols])

        brands = json.loads((SYN / "brand.json").read_text(encoding="utf-8"))
        cur.execute("TRUNCATE brand")
        cur.executemany("INSERT INTO brand VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(b["brand_id"], b["brand_name"], b["industry"], b["category"],
                          b["sub_category"], b["cooperation_count"], b["history_budget_total"])
                         for b in brands])

        deals = json.loads((SYN / "deal.json").read_text(encoding="utf-8"))
        cur.execute("TRUNCATE deal")
        cur.executemany("INSERT INTO deal VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(d["deal_id"], d["brand_id"], d["brand_name"], d["category"],
                          d["sub_category"], d["goal"], d["budget"], d["stage"], d["demand_desc"],
                          json.dumps(d["kol_ids"]), d["start_date"], d["end_date"], d["owner"],
                          json.dumps(d["result_metrics"]) if d["result_metrics"] else None,
                          d["created_at"], d["updated_at"]) for d in deals])

        fus = json.loads((SYN / "deal_followup.json").read_text(encoding="utf-8"))
        cur.execute("TRUNCATE deal_followup")
        cur.executemany("INSERT INTO deal_followup VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(f["id"], f["deal_id"], f["stage_from"], f["stage_to"], f["note"],
                          f["operator"], f["created_at"]) for f in fus])
    print(f"    达人 {len(kols)} | 品牌 {len(brands)} | 商单 {len(deals)} | 跟进 {len(fus)}")

    print("\n[2] 切分语料（父子块）")
    parents, chunks = build_chunks()
    print(f"    父块 {len(parents)} | 子块 {len(chunks)}")
    with con.cursor() as cur:
        cur.execute("TRUNCATE parent_chunk")
        cur.execute("TRUNCATE chunk_meta")
        cur.executemany("INSERT INTO parent_chunk VALUES (%s,%s,%s,%s,%s)",
                        [(p["parent_id"], p["doc_id"], p["doc_title"], p["section"], p["content"])
                         for p in parents])
        cur.executemany(
            "INSERT INTO chunk_meta VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(c["chunk_id"], c["parent_id"], c["doc_id"], c["doc_title"], c["doc_type"],
              c["source_file"], c["object_key"], c["section"], c["chunk_type"], c["category"],
              c["platform"], c["tier"], c["deal_year"], c["char_len"], c["content"])
             for c in chunks])
    print("    OK 父块与子块已入库")

    print("\n[3] 上传原件到 MinIO")
    files = sorted(RAW.rglob("*.md"))
    for p in files:
        rel = p.relative_to(RAW).as_posix()
        storage.upload_object(p, f"docs/{rel}")
    objs = list(storage.minio_client().list_objects(storage.MINIO_BUCKET, recursive=True))
    print(f"    OK 上传 {len(files)} 个原件，桶内共 {len(objs)} 个对象")
    try:
        url = storage.presigned_url("docs/rate_cards/美妆类目达人刊例表.md", 600)
        print(f"    预签名直链: {url[:110]}…")
    except Exception as e:
        print(f"    预签名失败: {str(e)[:100]}")

    print("\n[4] 本地向量化并写入 Milvus")
    client = storage.milvus_client()
    if client.has_collection(storage.COLLECTION):
        client.drop_collection(storage.COLLECTION)
    client.create_collection(storage.COLLECTION,
                             schema=storage.milvus_schema(client),
                             index_params=storage.milvus_index(client))
    print(f"    集合 {storage.COLLECTION} 已建（HNSW+COSINE + 5 个倒排索引）")

    B = 32
    total = 0
    t0 = time.time()
    for i in range(0, len(chunks), B):
        batch = chunks[i:i + B]
        vecs = llm.embed([c["content"] for c in batch])
        rows = []
        for c, v in zip(batch, vecs):
            r = dict(c)
            r["embedding"] = v
            rows.append(r)
        client.insert(storage.COLLECTION, rows)
        total += len(rows)
        print(f"    进度 {total}/{len(chunks)}  用时 {time.time()-t0:.0f}s", end="\r")
    client.flush(storage.COLLECTION)
    time.sleep(2)
    stats = client.get_collection_stats(storage.COLLECTION)
    print(f"\n    OK 写入 {stats['row_count']} 条向量（期望 {len(chunks)}），"
          f"总耗时 {time.time()-t0:.0f}s")

    con.close()
    print("\n" + "=" * 88)
    print("入库完成。")
    print("=" * 88)


if __name__ == "__main__":
    main()
