# -*- coding: utf-8 -*-
"""语料扩充后的基线漂移测量 + 加权 RRF 在生产管线内的效果验证。

背景：语料从 1809 块扩到 1974 块（+165 块叙述性方法论/政策内容）。
题库真值是按旧语料建的，需要先确认：
  ① 现有生产管线（A5，等权 RRF + 先验 + 配额）的指标是否漂移；
  ② 加权 RRF（向量 0.5 : 关键词 1.0）在**保留先验与配额的生产管线内**是否仍有效。

为什么不能直接用 experiment_final_config.py 的 F1 数字：
    F1 是实验脚本自己拼装的流程，跳过了生产 search() 里的 doc_type_prior
    与 type_quota 两个开关。F1 的 0.756 对应的是「关掉先验和配额」的配置，
    与线上跑的不是同一套东西。落代码前必须验证「生产管线 + 加权 RRF」的组合。

配置矩阵（全部走生产 Retriever.search，只改开关与权重）：
  P0_现行生产   等权 RRF + 先验 + 配额 + 过滤 + 重排（= 当前线上）
  P1_加权RRF    加权 RRF + 先验 + 配额 + 过滤 + 重排（只改融合权重）
  P2_加权去先验  加权 RRF + 无先验 + 配额 + 过滤 + 重排（隔离先验影响）
  P3_加权去配额  加权 RRF + 先验 + 无配额 + 过滤 + 重排（隔离配额影响）
  P4_F1等价    加权 RRF + 无先验 + 无配额 + 过滤 + 重排（对齐实验脚本 F1）

用法：
    .venv/Scripts/python.exe -u scripts/eval_corpus_expansion.py
产物：
    reports/eval_corpus_expansion.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import starmoyu.llm as llm  # noqa: E402
import starmoyu.retriever as R  # noqa: E402
from eval_v5_retrieval import metrics_for_ranked  # noqa: E402
from experiment_result_io import save_result  # noqa: E402

EVAL = ROOT / "reports" / "eval_set_v6.json"
OUT = ROOT / "reports" / "eval_corpus_expansion.json"
TOP_K = 10
CAND_K = 30
W_VEC, W_BM25 = 0.5, 1.0

# 嵌入结果缓存：同一问句在 5 个配置里重复调用，不缓存会跑 5 倍时间
_EMB_CACHE: dict = {}
_orig_embed = llm.embed


def cached_embed(texts, **kw):
    key = tuple(texts)
    if key not in _EMB_CACHE:
        _EMB_CACHE[key] = _orig_embed(texts, **kw)
    return _EMB_CACHE[key]


llm.embed = cached_embed


def patch_weighted_rrf(w_vec: float, w_bm25: float) -> None:
    """把生产 Retriever.rrf 换成加权版本。

    只改权重，融合公式仍是 RRF 的 1/(k+rank+1)，不是归一化分数线性相加。
    w=1.0 时与原来完全等价，用于确认改造本身没有引入偏差。
    """

    def weighted_rrf(rank_lists, k: int = 60):
        # 权重按调用顺序对应 rank_lists 的顺序；生产 search() 传的是
        # [vec_list, bm_list]，因此第一个列表用 w_vec，第二个用 w_bm25。
        weights = [w_vec, w_bm25]
        score: dict[str, float] = {}
        for i, rl in enumerate(rank_lists):
            w = weights[i] if i < len(weights) else 1.0
            for rank, cid in enumerate(rl):
                score[cid] = score.get(cid, 0.0) + w / (k + rank + 1)
        return score

    R.Retriever.rrf = staticmethod(weighted_rrf)


def run_config(items, retriever, *, use_prior: bool, use_quota: bool,
               w_vec: float, w_bm25: float) -> list[dict]:
    patch_weighted_rrf(w_vec, w_bm25)
    rows = []
    for it in items:
        out = retriever.search(
            it["query"], top_k=TOP_K, cand_k=CAND_K,
            use_bm25=True, use_vector=True, use_rrf=True, use_rerank=True,
            use_meta_filter=True, use_prior=use_prior, use_dedupe=True,
            use_quota=use_quota,
        )
        seq, seen = [], set()
        for c in out["results"]:
            if c.source_file not in seen:
                seen.add(c.source_file)
                seq.append(c.source_file)
            if len(seq) >= TOP_K:
                break
        m = metrics_for_ranked(seq, it["labels"])
        m["id"] = it["id"]
        m["layer"] = it["difficulty"]
        m["rerank_used"] = out["info"].get("rerank_used", False)
        rows.append(m)
    return rows


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    retriever = R.Retriever()
    print(f"语料 {len(retriever.chunks)} 块｜题目 {len(items)} 道｜"
          f"cand_k={CAND_K} top_k={TOP_K}", flush=True)

    cfgs = [
        # (配置名, use_prior, use_quota, w_vec, w_bm25)
        ("P0_现行生产_等权RRF_含先验配额", True, True, 1.0, 1.0),
        ("P1_加权RRF_含先验配额", True, True, W_VEC, W_BM25),
        ("P2_加权RRF_无先验_含配额", False, True, W_VEC, W_BM25),
        ("P3_加权RRF_含先验_无配额", True, False, W_VEC, W_BM25),
        ("P4_加权RRF_无先验_无配额_对齐F1", False, False, W_VEC, W_BM25),
    ]

    results = {}
    t0 = time.time()
    for name, use_prior, use_quota, w_vec, w_bm25 in cfgs:
        t1 = time.time()
        rows = run_config(items, retriever, use_prior=use_prior,
                          use_quota=use_quota, w_vec=w_vec, w_bm25=w_bm25)
        agg = {}
        for L in ("ALL", "L1", "L2", "L3"):
            sub = rows if L == "ALL" else [x for x in rows if x["layer"] == L]
            agg[L] = {"n": len(sub)}
            for k in ("hit1", "hit5", "hit10", "mrr", "recall10", "ndcg10"):
                agg[L][k] = round(sum(x[k] for x in sub) / len(sub), 4) if sub else 0.0
        results[name] = {
            "matrix": agg, "per_item": rows,
            "rerank_fail": sum(1 for x in rows if not x["rerank_used"]),
            "seconds": round(time.time() - t1, 1),
            "config": {"use_prior": use_prior, "use_quota": use_quota,
                       "w_vec": w_vec, "w_bm25": w_bm25},
        }
        a = agg["ALL"]
        print(f"  {name:<32} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} NDCG@10={a['ndcg10']:.4f} "
              f"失败={results[name]['rerank_fail']} {time.time()-t1:.0f}s", flush=True)

    save_result(OUT, {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": es["eval_set_version"],
        "corpus_chunks": len(retriever.chunks),
        "n_items": len(items), "cand_k": CAND_K, "top_k": TOP_K,
        "note": "语料扩充至 1974 块后的基线漂移与加权 RRF 生产管线验证；"
                "全部配置走生产 Retriever.search，只改开关与融合权重",
        "results": {k: {"matrix": v["matrix"], "rerank_fail": v["rerank_fail"],
                        "seconds": v["seconds"], "config": v["config"]}
                    for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
        "total_seconds": round(time.time() - t0, 1),
    }, __file__, label="corpus-expansion")

    print(f"\n总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
