# -*- coding: utf-8 -*-
"""v4 任务A：以 PG chunk_meta 为准全量重建 Milvus dm_chunks 集合。

背景：Milvus 2084 条 vs PG chunk_meta 2086 条，残留 2 条已删除块的向量，
会触发检索器键错误（v3 曾在评测脚本里用兜底过滤绕过，本轮从数据层根治）。

做法：
  1. 从 PG chunk_meta 全量读出（唯一数据源，绝不 TRUNCATE PG）；
  2. drop + recreate Milvus 集合（复用 storage.milvus_schema / milvus_index）；
  3. 用项目现有嵌入通道（本地 Ollama bge-m3，llm.embed）分批向量化写入；
  4. 校验：集合行数 == PG 行数 == 2086；加载 Retriever 后抽样检索无键错误。
绝不调用 src/starmoyu/ingest.py（会 TRUNCATE 业务表）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from starmoyu import llm, storage  # noqa: E402

LOG_PATH = ROOT / "reports" / "rebuild_milvus_v4.log"
LOG_LINES: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def main() -> None:
    t0 = time.time()
    log("== v4 Milvus 重建开始 ==")

    # [1] PG 全量读取
    pg = storage.pg_connect(storage.pg_ensure_database())
    cols = ("chunk_id,parent_id,doc_id,doc_title,doc_type,source_file,object_key,section,"
            "chunk_type,category,platform,tier,deal_year,char_len,content")
    rows = pg.execute(f"SELECT {cols} FROM chunk_meta").fetchall()
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM kol_profile")
        kol = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM deal")
        deal = cur.fetchone()[0]
    log(f"PG chunk_meta 共 {len(rows)} 条（kol_profile={kol}, deal={deal} 业务表未触碰）")

    # [2] 重建集合
    client = storage.milvus_client()
    if client.has_collection(storage.COLLECTION):
        n_before = client.get_collection_stats(storage.COLLECTION)["row_count"]
        client.drop_collection(storage.COLLECTION)
        log(f"已删除旧集合（原 {n_before} 条）")
    client.create_collection(storage.COLLECTION,
                             schema=storage.milvus_schema(client),
                             index_params=storage.milvus_index(client))
    log(f"集合 {storage.COLLECTION} 已重建")

    # [3] 分批向量化写入（项目现有通道：本地 Ollama bge-m3）
    B = 32
    total = 0
    for i in range(0, len(rows), B):
        batch = rows[i:i + B]
        vecs = llm.embed([r[14] for r in batch])
        payload = []
        for r, v in zip(batch, vecs):
            payload.append({
                "chunk_id": r[0], "embedding": v, "parent_id": r[1], "doc_id": r[2],
                "doc_title": r[3], "doc_type": r[4], "source_file": r[5],
                "object_key": r[6], "section": r[7], "chunk_type": r[8],
                "category": r[9] or "", "platform": r[10] or "", "tier": r[11] or "",
                "deal_year": r[12] or 0, "char_len": r[13] or 0, "content": r[14],
            })
        client.insert(storage.COLLECTION, payload)
        total += len(payload)
        if total % 320 == 0 or total == len(rows):
            log(f"写入进度 {total}/{len(rows)} 累计 {time.time()-t0:.0f}s")
    client.flush(storage.COLLECTION)
    time.sleep(3)
    n_after = client.get_collection_stats(storage.COLLECTION)["row_count"]
    log(f"Milvus 写入完成：{n_after} 条（PG {len(rows)} 条，一致={n_after == len(rows)}）")

    # [4] 检索层校验：加载 Retriever，抽样检索确认无键错误
    from starmoyu.retriever import Retriever
    r = Retriever()
    log(f"Retriever 加载：PG chunks={len(r.chunks)}, by_id={len(r.by_id)}")
    milvus_ids = set()
    for batch_start in range(0, 3000, 1000):
        try:
            it = client.query(storage.COLLECTION, filter="chunk_id != ''",
                              output_fields=["chunk_id"], limit=batch_start + 1000,
                              offset=batch_start)
            milvus_ids.update(x["chunk_id"] for x in it)
            if len(it) < 1000:
                break
        except Exception:
            break
    # 简化：直接全量 query
    milvus_ids = set()
    res = client.query(storage.COLLECTION, filter="chunk_id != ''",
                       output_fields=["chunk_id"], limit=3000)
    milvus_ids.update(x["chunk_id"] for x in res)
    only_milvus = milvus_ids - set(r.by_id)
    only_pg = set(r.by_id) - milvus_ids
    log(f"集合 ID 数={len(milvus_ids)}；仅在 Milvus: {len(only_milvus)}；仅在 PG: {len(only_pg)}")
    if only_milvus or only_pg:
        log(f"!! 不一致：milvus_only={sorted(only_milvus)[:10]} pg_only={sorted(only_pg)[:10]}")

    # 抽样检索：10 条评测集问题跑纯向量召回，验证无键错误且 rerank 通道正常
    import json
    es = json.loads((ROOT / "reports" / "eval_set_v3.json").read_text(encoding="utf-8"))
    probes = [it["query"] for it in es["items"] if it["difficulty"] != "L4"][::12][:10]
    key_err = 0
    for q in probes:
        vec_hits = r.retrieve_vector(q, 10)
        missing = [cid for cid, _ in vec_hits if cid not in r.by_id]
        if missing:
            key_err += 1
            log(f"!! 键错误: {q[:30]} -> {missing}")
    log(f"抽样检索 {len(probes)} 条，键错误 {key_err} 条")
    ok, msg = llm.rerank_available()
    log(f"rerank 通道探测: {ok} {msg}")

    ok2, _ = client.describe_collection(storage.COLLECTION), None
    verdict = (n_after == len(rows) and not only_milvus and not only_pg and key_err == 0)
    log(f"== 重建结论: {'通过' if verdict else '未通过'}，总耗时 {time.time()-t0:.0f}s ==")
    pg.close()
    client.close()
    LOG_PATH.write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")
    sys.exit(0 if verdict else 1)


if __name__ == "__main__":
    main()
