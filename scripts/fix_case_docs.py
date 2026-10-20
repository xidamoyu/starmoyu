"""结案案例语料一致性修复（一次性运维脚本）。

背景：
    2026-10 全库重建（14289 达人 / 400 商单）后，`data/raw/deal_cases/` 里仍留着
    上一代语料的案例文档。核对发现两类污染：
      1) 孤儿案例：对应商单在 `deal` 表已不存在（重建时被替换掉）
      2) 状态不符：对应商单存在但 stage 不是「结案」，或无 result_metrics
    这两类文档仍留在磁盘 / PG chunk_meta / Milvus 里，会被检索命中并当作真实案例引用。

    同时修复渲染编号跳号缺陷：无「合作画像」段时「跟进大事记」编号由「六」改为「五」。

做法：
    - 以「结案 且 result_metrics 非空」的商单为唯一权威集合
    - 逐单重新渲染案例文档（覆盖写，含编号修复）
    - 逐单增量入库（先删旧块再插，幂等）
    - 集合外的案例文档：删磁盘文件 + 删 chunk_meta / parent_chunk / Milvus 向量

只动 chunk 表、Milvus、deal_cases 目录，绝不碰结构表（kol_profile / deal / brand 等）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server import sediment_service as ss  # noqa: E402
from starmoyu import storage  # noqa: E402


def authoritative_deals() -> list[str]:
    """结案且带效果数据的商单 = 案例文档的唯一合法集合。"""
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT deal_id FROM deal "
                "WHERE stage = '结案' AND result_metrics IS NOT NULL "
                "AND result_metrics::text <> '{}' ORDER BY deal_id"
            )
            return [r[0] for r in cur.fetchall()]


def drop_document(rel_path: str) -> dict:
    """从 PG 与 Milvus 彻底删除一份文档的全部块。"""
    doc_id = rel_path.replace("/", "__").replace(".md", "")
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT chunk_id FROM chunk_meta WHERE source_file = %s", (rel_path,))
            ids = [r[0] for r in cur.fetchall()]
            if ids:
                ph = ",".join(["%s"] * len(ids))
                cur.execute(f"DELETE FROM chunk_meta WHERE chunk_id IN ({ph})", tuple(ids))  # noqa: S608
            cur.execute("DELETE FROM parent_chunk WHERE doc_id = %s", (doc_id,))
        con.commit()

    if ids:
        client = storage.milvus_client()
        client.delete(storage.COLLECTION, ids=ids)
        client.flush(storage.COLLECTION)

    p = ROOT / "data" / "raw" / rel_path
    if p.exists():
        p.unlink()
    return {"rel_path": rel_path, "removed_chunks": len(ids)}


def main() -> None:
    print("=" * 78)
    print("结案案例语料一致性修复")
    print("=" * 78)

    valid = authoritative_deals()
    valid_set = set(valid)
    print(f"[1] 权威集合：结案且有 result_metrics 的商单 = {len(valid)} 单")

    case_dir = ROOT / "data" / "raw" / "deal_cases"
    on_disk = sorted(case_dir.glob("*.md"))
    disk_ids: dict[str, Path] = {}
    for p in on_disk:
        m = re.match(r"(DC\d+)_", p.name)
        if m:
            disk_ids[m.group(1)] = p
    print(f"[2] 磁盘现有案例文档 = {len(on_disk)} 篇")

    # ---- 孤儿 / 状态不符：删除
    orphans = [f"deal_cases/{p.name}" for did, p in disk_ids.items() if did not in valid_set]
    print(f"[3] 需删除的污染文档 = {len(orphans)} 篇")
    removed_chunks = 0
    for rel in orphans:
        r = drop_document(rel)
        removed_chunks += r["removed_chunks"]
        if removed_chunks <= 20:
            print(f"    - 删除 {rel}（{r['removed_chunks']} 块）")
    print(f"    合计删除子块 {removed_chunks} 个")

    # ---- 合法集合：重渲染 + 增量入库
    print(f"[4] 重渲染并入{'库'}合法文档 = {len(valid)} 篇")
    rerendered = regested = 0
    total_chunks = 0
    for did in valid:
        r = ss.render_case_md(did)
        if r is None:
            print(f"    ! {did} 渲染失败（跳过）")
            continue
        rel, title, content = r
        rerendered += 1
        res = ss.ingest_document_incremental(rel, title, content)
        if res.get("ok"):
            regested += 1
            total_chunks += res["chunks"]
        else:
            print(f"    ! {did} 入库失败: {res}")
    print(f"    重渲染 {rerendered} 篇 / 入库 {regested} 篇 / 子块合计 {total_chunks}")

    # ---- 收尾核对
    print("[5] 收尾核对")
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chunk_meta WHERE source_file LIKE 'deal_cases/%_案例.md'")
            case_chunks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT source_file) FROM chunk_meta WHERE source_file LIKE 'deal_cases/%_案例.md'")
            case_docs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM chunk_meta")
            all_chunks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM parent_chunk")
            all_parents = cur.fetchone()[0]
    files_now = len(list(case_dir.glob("*.md")))
    print(f"    磁盘案例文档      : {files_now}")
    print(f"    PG 案例文档       : {case_docs}")
    print(f"    PG 案例子块       : {case_chunks}")
    print(f"    PG 全部子块       : {all_chunks}")
    print(f"    PG 全部父块       : {all_parents}")

    client = storage.milvus_client()
    stats = client.get_collection_stats(storage.COLLECTION)
    print(f"    Milvus 向量总数   : {stats.get('row_count')}")

    ok = files_now == case_docs == len(valid)
    print("=" * 78)
    print(f"结论：{'一致 ✓' if ok else '仍不一致 ✗'}（磁盘 {files_now} / PG {case_docs} / 权威 {len(valid)}）")
    print("=" * 78)


if __name__ == "__main__":
    main()
