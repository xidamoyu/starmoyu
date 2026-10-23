# -*- coding: utf-8 -*-
"""语料一致性检查：比对 PG chunk_meta 与 Milvus 的实际 chunk_id 集合。

为什么不用 row_count 比对：
    Milvus 的 `get_collection_stats()['row_count']` 把已标记删除但尚未 compact
    的墓碑记录也算进去。增量入库（ingest_document_incremental）会先删旧块再插
    新块，每次重入同一文档都会留下墓碑，于是 row_count 会持续高于真实数据量。
    实测：row_count 1848 vs PG 1842，但两侧 chunk_id 集合完全相同、零差异。
    因此 row_count 不能作为一致性判据，必须比对 ID 集合。

用法：
    .venv/Scripts/python.exe -u scripts/check_corpus_consistency.py
产物：
    reports/corpus_consistency.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import starmoyu.storage as storage  # noqa: E402
from experiment_result_io import save_result  # noqa: E402

OUT = ROOT / "reports" / "corpus_consistency.json"


def main() -> None:
    col = storage.COLLECTION
    client = storage.milvus_client()

    row_count = (client.get_collection_stats(col) or {}).get("row_count")
    # Milvus 单次 query 的 offset+limit 上限是 16384，语料超过此值必须分页取
    milvus_ids: set[str] = set()
    PAGE = 10000
    offset = 0
    while True:
        res = client.query(col, filter='chunk_id != ""',
                           output_fields=["chunk_id"], limit=PAGE, offset=offset)
        if not res:
            break
        milvus_ids.update(r["chunk_id"] for r in res)
        if len(res) < PAGE:
            break
        offset += PAGE

    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT chunk_id FROM chunk_meta")
            pg_ids = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT doc_type, count(*) FROM chunk_meta GROUP BY doc_type")
            by_type = dict(cur.fetchall())
            cur.execute("SELECT count(DISTINCT source_file) FROM chunk_meta")
            docs = cur.fetchone()[0]

    only_milvus = sorted(milvus_ids - pg_ids)
    only_pg = sorted(pg_ids - milvus_ids)
    consistent = not only_milvus and not only_pg

    print(f"Milvus row_count（含墓碑）: {row_count}")
    print(f"Milvus 实际 chunk_id 数   : {len(milvus_ids)}")
    print(f"PG chunk_meta 数          : {len(pg_ids)}")
    print(f"文档数                    : {docs}")
    print(f"只在 Milvus               : {len(only_milvus)}")
    print(f"只在 PG                   : {len(only_pg)}")
    print(f"墓碑数（row_count 差值）   : {(row_count or 0) - len(milvus_ids)}")
    print(f"结论                      : {'一致' if consistent else '不一致'}")
    if not consistent:
        print(f"  只在 Milvus: {only_milvus[:20]}")
        print(f"  只在 PG    : {only_pg[:20]}")

    save_result(OUT, {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "milvus_row_count_with_tombstones": row_count,
        "milvus_actual_ids": len(milvus_ids),
        "pg_chunk_meta": len(pg_ids),
        "documents": docs,
        "by_doc_type": by_type,
        "tombstones": (row_count or 0) - len(milvus_ids),
        "only_in_milvus": only_milvus[:50],
        "only_in_pg": only_pg[:50],
        "consistent": consistent,
        "note": "row_count 含未 compact 的墓碑记录，一致性判据是 ID 集合而非 row_count",
    }, __file__, label="corpus-consistency")


if __name__ == "__main__":
    main()
