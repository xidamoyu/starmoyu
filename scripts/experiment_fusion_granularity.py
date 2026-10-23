# -*- coding: utf-8 -*-
"""重构方向验证：块级融合 vs 文档级聚合融合。

核心假设：语料 434 篇文档 / 1809 块，平均每篇 4.17 块、最多 19 块。
当前管线的 RRF 在「块」上做等权融合，30 条候选池里同一篇文档可能占十几条，
把候选窗口的容量浪费在重复内容上，真实的不同文档数量被压缩到远小于 30。
此外两路的块级排名并不等价：BM25 在一篇长文档里命中多个块会重复加分，
向量在同一文档的多个语义相似块上也会聚集。

本实验对比四种融合粒度，全部在文档级输出（与主评测口径一致）：

  B1 块级 RRF 等权（现状）
  B2 块级 RRF 加权（向量 0.5 : 关键词 1.0，取 experiment_rrf_weight 的次优档）
  D1 文档级 RRF：先各自把块分聚到文档（取该文档最好块的分数做文档分），再对文档排名做 RRF
  D2 文档级 RRF + 文档分归一：文档分 = 该文档所有命中块分数之和（饱和归一），再 RRF

用法：.venv/Scripts/python.exe -u scripts/experiment_fusion_granularity.py
产物：reports/experiment_fusion_granularity.json / .log
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import starmoyu.llm as llm  # noqa: E402
from starmoyu.retriever import Retriever  # noqa: E402
from eval_v5_retrieval import metrics_for_ranked  # noqa: E402

_EMB_CACHE: dict = {}
_orig_embed = llm.embed


def cached_embed(texts, **kw):
    key = tuple(texts)
    if key not in _EMB_CACHE:
        _EMB_CACHE[key] = _orig_embed(texts, **kw)
    return _EMB_CACHE[key]


llm.embed = cached_embed

EVAL = ROOT / "reports" / "eval_set_v6.json"
OUT = ROOT / "reports" / "experiment_fusion_granularity.json"
DEPTH = 100     # 每路取多深
TOP_K = 10


def rrf(scores: dict[str, float], k: int = 60, w: float = 1.0) -> dict[str, float]:
    for rank, (key, _) in enumerate(sorted(scores.items(), key=lambda x: -x[1])):
        scores[key] = w / (k + rank + 1)
    return scores


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块 / {len(set(doc_of.values()))} 篇｜题目 {len(items)} 道", flush=True)

    configs = ["B1_块级RRF等权", "B2_块级RRF加权0.5:1", "D1_文档级RRF_取最好块",
               "D2_文档级RRF_块分求和", "D3_文档级RRF加权0.5:1"]
    rows_by_cfg: dict[str, list] = {c: [] for c in configs}
    t0 = time.time()

    for n, it in enumerate(items, 1):
        q = it["query"]
        raw_v = r.retrieve_vector(q, DEPTH)
        raw_b = r.retrieve_bm25(q, DEPTH)

        # ---- 块级分（原始分归一化到 0-1，避免两路量纲不同）
        def norm(hits: list[tuple[str, float]]) -> dict[str, float]:
            if not hits:
                return {}
            vals = [s for _, s in hits]
            lo, hi = min(vals), max(vals)
            span = (hi - lo) or 1.0
            return {c: (s - lo) / span for c, s in hits}

        v_sc, b_sc = norm(raw_v), norm(raw_b)
        v_chunks = sorted(v_sc, key=lambda c: -v_sc[c])
        b_chunks = sorted(b_sc, key=lambda c: -b_sc[c])

        # ---- B1：块级 RRF 等权
        b1 = {}
        for lst in (v_chunks, b_chunks):
            for rank, cid in enumerate(lst):
                b1[cid] = b1.get(cid, 0.0) + 1.0 / (60 + rank + 1)

        # ---- B2：块级 RRF 加权
        b2 = {}
        for lst, w in ((v_chunks, 0.5), (b_chunks, 1.0)):
            for rank, cid in enumerate(lst):
                b2[cid] = b2.get(cid, 0.0) + w / (60 + rank + 1)

        # ---- 文档级聚合：把块分聚到文档
        def to_docs(chunks: list[str], sc: dict[str, float], mode: str) -> dict[str, float]:
            agg: dict[str, float] = defaultdict(float)
            for cid in chunks:
                d = doc_of.get(cid)
                if not d:
                    continue
                if mode == "max":
                    agg[d] = max(agg.get(d, 0.0), sc.get(cid, 0.0))
                else:  # sum
                    agg[d] += sc.get(cid, 0.0)
            return dict(agg)

        v_max, b_max = to_docs(v_chunks, v_sc, "max"), to_docs(b_chunks, b_sc, "max")
        v_sum, b_sum = to_docs(v_chunks, v_sc, "sum"), to_docs(b_chunks, b_sc, "sum")

        def doc_rrf(a: dict[str, float], b: dict[str, float], w_a: float = 1.0, w_b: float = 1.0) -> list[str]:
            sa = {d: 0.0 for d in a}
            for rank, (d, _) in enumerate(sorted(a.items(), key=lambda x: -x[1])):
                sa[d] = w_a / (60 + rank + 1)
            for rank, (d, _) in enumerate(sorted(b.items(), key=lambda x: -x[1])):
                sa[d] = sa.get(d, 0.0) + w_b / (60 + rank + 1)
            return sorted(sa, key=lambda d: -sa[d])

        def chunks_to_docs(order: dict[str, float]) -> list[str]:
            seq, seen = [], set()
            for cid in sorted(order, key=lambda c: -order[c]):
                d = doc_of.get(cid)
                if d and d not in seen:
                    seen.add(d)
                    seq.append(d)
            return seq

        ranked = {
            "B1_块级RRF等权": chunks_to_docs(b1)[:TOP_K],
            "B2_块级RRF加权0.5:1": chunks_to_docs(b2)[:TOP_K],
            "D1_文档级RRF_取最好块": doc_rrf(v_max, b_max)[:TOP_K],
            "D2_文档级RRF_块分求和": doc_rrf(v_sum, b_sum)[:TOP_K],
            "D3_文档级RRF加权0.5:1": doc_rrf(v_max, b_max, 0.5, 1.0)[:TOP_K],
        }
        for cfg in configs:
            m = metrics_for_ranked(ranked[cfg], it["labels"])
            m["id"] = it["id"]
            m["layer"] = it["difficulty"]
            rows_by_cfg[cfg].append(m)

        if n % 25 == 0:
            print(f"  已跑 {n}/{len(items)}（{time.time()-t0:.0f}s）", flush=True)

    results = {}
    for cfg, rows in rows_by_cfg.items():
        agg = {}
        for L in ("ALL", "L1", "L2", "L3"):
            sub = rows if L == "ALL" else [x for x in rows if x["layer"] == L]
            agg[L] = {"n": len(sub)}
            for k in ("hit1", "hit5", "hit10", "mrr", "recall10", "ndcg10"):
                agg[L][k] = round(sum(x[k] for x in sub) / len(sub), 4) if sub else 0.0
        results[cfg] = {"matrix": agg, "per_item": rows}

    OUT.write_text(json.dumps({
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": es["eval_set_version"], "corpus_chunks": len(r.chunks),
        "n_items": len(items), "depth": DEPTH,
        "note": "对比块级与文档级融合粒度；不含重排序，用于判断重构方向",
        "results": {k: {"matrix": v["matrix"]} for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 融合粒度对比（不含重排序，文档级指标）===")
    for cfg in configs:
        a = results[cfg]["matrix"]["ALL"]
        print(f"  {cfg:<26} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f} NDCG@10={a['ndcg10']:.4f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
