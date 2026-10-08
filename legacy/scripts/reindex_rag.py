"""RAG 语料重新入库（v2 真实数据）。
只重建 chunk_meta / parent_chunk / Milvus 向量——**绝不触碰** kol_profile/deal/brand/deal_followup。
复用 ingest.py 的 build_chunks() 切分/打标逻辑。
"""
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from starmoyu import storage, llm  # noqa: E402
from starmoyu.ingest import build_chunks  # noqa: E402


def main():
    h = storage.health()
    for k, v in h.items():
        print(f"  {'OK ' if v['ok'] else 'ERR'} {k}: {v.get('error') or v}")
    if not all(v["ok"] for v in h.values()):
        print("存储组件不可用，终止。"); return

    print("\n[1] 切分语料（父子块）—— 来自 data/raw 真实渲染文档")
    parents, chunks = build_chunks()
    print(f"    父块 {len(parents)} | 子块 {len(chunks)}")

    print("\n[2] 重建 PG chunk 表（仅 chunk_meta/parent_chunk）")
    with storage.pg_connect() as con:
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
        con.commit()
    print("    OK chunk 表已重建（结构表未动）")

    print("\n[3] 重建 Milvus 集合")
    client = storage.milvus_client()
    if client.has_collection(storage.COLLECTION):
        client.drop_collection(storage.COLLECTION)
    client.create_collection(storage.COLLECTION,
                             schema=storage.milvus_schema(client),
                             index_params=storage.milvus_index(client))
    print(f"    集合 {storage.COLLECTION} 已重建")

    print("\n[4] 向量化写入")
    B = 32; total = 0; t0 = time.time()
    for i in range(0, len(chunks), B):
        batch = chunks[i:i + B]
        vecs = llm.embed([c["content"] for c in batch])
        rows = []
        for c, v in zip(batch, vecs):
            r = dict(c); r["embedding"] = v; rows.append(r)
        client.insert(storage.COLLECTION, rows)
        total += len(rows)
        print(f"    进度 {total}/{len(chunks)}  用时 {time.time()-t0:.0f}s", end="\r")
    client.flush(storage.COLLECTION)
    time.sleep(2)
    stats = client.get_collection_stats(storage.COLLECTION)
    print(f"\n    OK 写入 {stats['row_count']} 条向量（期望 {len(chunks)}），总耗时 {time.time()-t0:.0f}s")

    # 结构表计数确认未被影响
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT count(*) FROM kol_profile"); kol = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM deal"); deal = cur.fetchone()[0]
    print(f"\n[5] 结构表确认未被影响: kol_profile={kol} deal={deal}")
    print("\n完成。")


if __name__ == "__main__":
    main()
