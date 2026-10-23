# -*- coding: utf-8 -*-
"""检索管线诊断：定位各通道的真实能力边界，为重构方向提供依据。

要回答四个问题：
1. 纯向量通道是真值「召回不到」还是「排得靠后」？看真值文档在向量全库排名中的位置分布。
2. 纯 BM25 通道同样看位置分布。
3. 两路的互补性：并集能覆盖多少题的真值文档？并集里真值的排名能到多少？
4. 块级融合的问题有多大：同一文档平均有几个块进候选池，块级 RRF 是否被同文档多块刷屏。

用法：.venv/Scripts/python.exe -u scripts/diagnose_retrieval.py
产物：reports/diagnose_retrieval.json
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import starmoyu.llm as llm  # noqa: E402
from starmoyu.retriever import Retriever  # noqa: E402

_EMB_CACHE: dict = {}
_orig_embed = llm.embed


def cached_embed(texts, **kw):
    key = tuple(texts)
    if key not in _EMB_CACHE:
        _EMB_CACHE[key] = _orig_embed(texts, **kw)
    return _EMB_CACHE[key]


llm.embed = cached_embed

EVAL = ROOT / "reports" / "eval_set_v6.json"
OUT = ROOT / "reports" / "diagnose_retrieval.json"
DEPTH = 100  # 诊断深度：看真值在排名第几位


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块 / {len(set(doc_of.values()))} 篇文档｜题目 {len(items)} 道", flush=True)

    # ---- 每篇文档的块数（看同文档多块刷屏的严重程度）
    blocks_per_doc = Counter(doc_of.values())
    print(f"每篇文档块数：均值 {sum(blocks_per_doc.values())/len(blocks_per_doc):.2f} "
          f"最大 {max(blocks_per_doc.values())}", flush=True)

    rows = []
    t0 = time.time()
    for n, it in enumerate(items, 1):
        q = it["query"]
        vec = [c for c, _ in r.retrieve_vector(q, DEPTH)]
        bm = [c for c, _ in r.retrieve_bm25(q, DEPTH)]

        def doc_rank_list(chunk_ids: list[str]) -> list[str]:
            """块级排名 → 文档级首次出现顺序（这才是文档级召回的真实深度）。"""
            seq, seen = [], set()
            for cid in chunk_ids:
                d = doc_of.get(cid)
                if d and d not in seen:
                    seen.add(d)
                    seq.append(d)
            return seq

        vdocs, bdocs = doc_rank_list(vec), doc_rank_list(bm)
        direct = {f for f, v in it["labels"].items() if v == "direct"}
        allrel = set(it["labels"])

        def first_rank(seq: list[str], targets: set[str]) -> int | None:
            for i, d in enumerate(seq, 1):
                if d in targets:
                    return i
            return None

        rec = {
            "id": it["id"], "layer": it["difficulty"],
            "vec_rank": first_rank(vdocs, direct),
            "bm_rank": first_rank(bdocs, direct),
            "vec_docs": len(vdocs), "bm_docs": len(bdocs),
            "n_direct": len(direct), "n_all": len(allrel),
            # 真值文档的各个块在该路排名里出现几次（同文档刷屏强度）
            "vec_dup": sum(1 for c in vec[:30] if doc_of.get(c) in direct),
            "bm_dup": sum(1 for c in bm[:30] if doc_of.get(c) in direct),
            # 并集：两路 top-30 文档并起来，真值是否被覆盖
            "union_covers": bool((set(vdocs[:30]) | set(bdocs[:30])) & direct),
        }
        rows.append(rec)
        if n % 50 == 0:
            print(f"  已诊断 {n}/{len(items)} 题（{time.time()-t0:.0f}s）", flush=True)

    # ---- 汇总
    def dist(key: str) -> dict:
        vals = [x[key] for x in rows]
        found = [v for v in vals if v is not None]
        return {
            "n": len(vals), "found": len(found),
            "found_rate": round(len(found) / len(vals), 4),
            "hit1": round(sum(1 for v in found if v == 1) / len(vals), 4),
            "top5": round(sum(1 for v in found if v <= 5) / len(vals), 4),
            "top10": round(sum(1 for v in found if v <= 10) / len(vals), 4),
            "top30": round(sum(1 for v in found if v <= 30) / len(vals), 4),
            "top100": round(len(found) / len(vals), 4),
            "median_rank": sorted(found)[len(found) // 2] if found else None,
        }

    summary = {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus_chunks": len(r.chunks),
        "corpus_docs": len(set(doc_of.values())),
        "n_items": len(items),
        "depth": DEPTH,
        "vector": dist("vec_rank"),
        "bm25": dist("bm_rank"),
        "union_covers_top30": round(sum(1 for x in rows if x["union_covers"]) / len(items), 4),
        # 注意：这两项统计的是 DEPTH(=100) 深度内的去重文档数，不是 top30
        "avg_docs_in_vec_depth": round(sum(x["vec_docs"] for x in rows) / len(items), 2),
        "avg_docs_in_bm_depth": round(sum(x["bm_docs"] for x in rows) / len(items), 2),
        "avg_direct_blocks_in_vec_top30": round(sum(x["vec_dup"] for x in rows) / len(items), 2),
        "avg_direct_blocks_in_bm_top30": round(sum(x["bm_dup"] for x in rows) / len(items), 2),
        "by_layer": {},
        "per_item": rows,
    }
    for L in ("L1", "L2", "L3"):
        sub = [x for x in rows if x["layer"] == L]
        summary["by_layer"][L] = {
            "n": len(sub),
            "vec_top1": round(sum(1 for x in sub if x["vec_rank"] == 1) / len(sub), 4),
            "vec_top10": round(sum(1 for x in sub if x["vec_rank"] and x["vec_rank"] <= 10) / len(sub), 4),
            "vec_top30": round(sum(1 for x in sub if x["vec_rank"] and x["vec_rank"] <= 30) / len(sub), 4),
            "bm_top1": round(sum(1 for x in sub if x["bm_rank"] == 1) / len(sub), 4),
            "bm_top10": round(sum(1 for x in sub if x["bm_rank"] and x["bm_rank"] <= 10) / len(sub), 4),
            "bm_top30": round(sum(1 for x in sub if x["bm_rank"] and x["bm_rank"] <= 30) / len(sub), 4),
            "union_top30": round(sum(1 for x in sub if x["union_covers"]) / len(sub), 4),
        }

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 文档级召回深度（真值文档在该通道排名内的比例）===")
    for name in ("vector", "bm25"):
        s = summary[name]
        print(f"  {name:<7} top1={s['hit1']:.3f} top5={s['top5']:.3f} top10={s['top10']:.3f} "
              f"top30={s['top30']:.3f} top100={s['top100']:.3f} 中位排名={s['median_rank']}")
    print(f"\n  两路 top30 并集覆盖真值：{summary['union_covers_top30']:.3f}")
    print(f"  深度 {DEPTH} 内去重文档数：向量 {summary['avg_docs_in_vec_depth']:.1f} 篇 / "
          f"BM25 {summary['avg_docs_in_bm_depth']:.1f} 篇"
          f"（即约 {DEPTH - summary['avg_docs_in_vec_depth']:.0f} 个块是同文档重复）")
    print(f"  top30 内属于真值的块数：向量 {summary['avg_direct_blocks_in_vec_top30']:.2f} / "
          f"BM25 {summary['avg_direct_blocks_in_bm_top30']:.2f}")
    print("\n=== 分层 ===")
    for L, s in summary["by_layer"].items():
        print(f"  {L} n={s['n']:3d} | 向量 top1/top10/top30 = "
              f"{s['vec_top1']:.3f}/{s['vec_top10']:.3f}/{s['vec_top30']:.3f} | "
              f"BM25 = {s['bm_top1']:.3f}/{s['bm_top10']:.3f}/{s['bm_top30']:.3f} | "
              f"并集top30={s['union_top30']:.3f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
