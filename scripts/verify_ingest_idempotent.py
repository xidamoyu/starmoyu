"""语料重传幂等自检（可重复执行，不改业务表以外的数据）。

验证三件事：
  1. 同一文件名重传（内容已改）→ 旧块必须被清掉，不产生重复文档
  2. 完全相同的重复入库 → 块数不变（真幂等）
  3. 入库后 PG chunk_meta 与 Milvus 向量两侧 chunk_id 一致（无孤儿/无缺向量）

用法：
    .venv/Scripts/python.exe scripts/verify_ingest_idempotent.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server.sediment_service import (  # noqa: E402
    ingest_document_incremental, rel_path_for_filename,
)
from starmoyu import storage  # noqa: E402

FNAME = "幂等自检-临时文档.md"
REL = rel_path_for_filename(FNAME)

V1 = """# 幂等自检文档

## 一、第一版内容

第一版独有关键词：青柠协议。

## 二、第一版补充

第一版第二个关键词：琥珀条款。
"""

V2 = """# 幂等自检文档

## 一、第二版内容

第二版独有关键词：松塔协议。内容已改写，长度也不同。

## 二、第二版补充

第二版第二个关键词：琉璃条款。

## 三、第二版新增章节

新增一个章节用于验证父块数量变化时旧父块会被清掉。
"""


def pg_chunk_ids(rel: str) -> set[str]:
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT chunk_id FROM chunk_meta WHERE source_file=%s", (rel,))
            return {r[0] for r in cur.fetchall()}


def pg_parent_ids(doc_id: str) -> set[str]:
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT parent_id FROM parent_chunk WHERE doc_id=%s", (doc_id,))
            return {r[0] for r in cur.fetchall()}


def milvus_has(chunk_ids: set[str]) -> set[str]:
    if not chunk_ids:
        return set()
    c = storage.milvus_client()
    c.load_collection(storage.COLLECTION)
    ph = ",".join(f'"{i}"' for i in chunk_ids)
    rows = list(c.query(storage.COLLECTION, filter=f"chunk_id in [{ph}]",
                        output_fields=["chunk_id"], limit=len(chunk_ids) + 10))
    return {r["chunk_id"] for r in rows}


def cleanup() -> None:
    """清掉自检文档（PG + Milvus + 磁盘文件）。"""
    ids = pg_chunk_ids(REL)
    if ids:
        c = storage.milvus_client()
        c.delete(storage.COLLECTION, ids=list(ids))
        c.flush(storage.COLLECTION)
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("DELETE FROM chunk_meta WHERE source_file=%s", (REL,))
            cur.execute("DELETE FROM parent_chunk WHERE doc_id=%s",
                        (REL.replace("/", "__").replace(".md", ""),))
        con.commit()
    p = ROOT / "data" / "raw" / REL
    if p.exists():
        p.unlink()


def main() -> int:
    print(f"目标语料路径: {REL}")
    cleanup()   # 先清历史残留，保证从零开始
    fails: list[str] = []

    # --- 第 1 次入库（第一版） ---
    r1 = ingest_document_incremental(REL, "幂等自检文档", V1)
    ids1 = pg_chunk_ids(REL)
    print(f"[第1次入库] chunks={r1['chunks']} replaced_old={r1['replaced_old']}")
    if not ids1:
        fails.append("第1次入库后 PG 无块")

    # --- 第 2 次入库（同一内容，验证真幂等） ---
    before = pg_chunk_ids(REL)
    r2 = ingest_document_incremental(REL, "幂等自检文档", V1)
    after = pg_chunk_ids(REL)
    print(f"[第2次入库·同内容] chunks={r2['chunks']} replaced_old={r2['replaced_old']} "
          f"块数 {len(before)}→{len(after)}")
    if before != after:
        fails.append(f"同内容重入块集变化：{len(before)}→{len(after)}")
    if r2["replaced_old"] != len(before):
        fails.append(f"replaced_old 计数不符：{r2['replaced_old']} != {len(before)}")

    # --- 第 3 次入库（同名文件、内容已改，验证旧块清理） ---
    r3 = ingest_document_incremental(REL, "幂等自检文档", V2)
    ids3 = pg_chunk_ids(REL)
    leftover = ids1 - ids3          # 第一版块若仍在 = 没清干净
    print(f"[第3次入库·改内容] chunks={r3['chunks']} replaced_old={r3['replaced_old']} "
          f"旧块残留={len(leftover)}")
    if leftover:
        fails.append(f"旧块未清干净，残留 {len(leftover)} 块：{sorted(leftover)[:3]}")

    # 旧版独有关键词必须检索不到
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("""SELECT count(*) FROM chunk_meta
                           WHERE source_file=%s AND content LIKE %s""",
                        (REL, "%青柠协议%"))
            stale = cur.fetchone()[0]
    if stale:
        fails.append(f"旧版关键词仍存在：{stale} 块")
    print(f"[旧内容检查] 含旧关键词『青柠协议』的块数={stale}（应为 0）")

    # --- 两侧一致性 ---
    ids_pg = pg_chunk_ids(REL)
    ids_mv = milvus_has(ids_pg)
    print(f"[一致性] PG={len(ids_pg)} Milvus={len(ids_mv)}")
    if ids_pg - ids_mv:
        fails.append(f"PG 有块但 Milvus 缺向量：{len(ids_pg - ids_mv)}")
    if ids_mv - ids_pg:
        fails.append(f"Milvus 有孤儿向量：{len(ids_mv - ids_pg)}")

    # --- 父块不残留（第三版比第一版多一节） ---
    doc_id = REL.replace("/", "__").replace(".md", "")
    parents = pg_parent_ids(doc_id)
    print(f"[父块] doc_id={doc_id} parents={len(parents)}")
    if not parents:
        fails.append("父块为空")

    cleanup()
    print(f"[清理] 已删除自检语料 {REL}")

    print()
    if fails:
        print(f"❌ 自检失败 {len(fails)} 项")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 幂等自检全部通过：同名改内容重传会清旧块，同内容重入块数不变，两侧一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
