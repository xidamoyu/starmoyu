# -*- coding: utf-8 -*-
"""检索评测脚本（v3，独立实现）：五指标 + 难度分层 + 五组消融。

评测集：reports/eval_set_v3.json（与本脚本同期独立构建）
指标计算为本脚本独立实现，不复用任何历史评测代码与历史数字。
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu")
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import llm  # noqa: E402

# ---- 查询向量缓存：同一查询在 5 组消融中重复出现，避免重复调用本地向量化服务 ----
_EMB_CACHE: dict[tuple, list] = {}
_orig_embed = llm.embed


def cached_embed(texts, **kw):
    key = tuple(texts)
    if key not in _EMB_CACHE:
        _EMB_CACHE[key] = _orig_embed(texts, **kw)
    return _EMB_CACHE[key]


llm.embed = cached_embed

from starmoyu.retriever import Retriever  # noqa: E402

# ---- Milvus/PG 数据一致性兜底：Milvus 集合可能残留已从 PG 删除的块（本轮实测差 2 条），
# 向量召回会带回 by_id 中不存在的 chunk_id。在 Retriever 层外做过滤，保持源码不动。 ----
_orig_retrieve_vector = Retriever.retrieve_vector


def safe_retrieve_vector(self, query: str, top_k: int):
    hits = _orig_retrieve_vector(self, query, top_k)
    return [(cid, s) for cid, s in hits if cid in self.by_id]


Retriever.retrieve_vector = safe_retrieve_vector

EVAL_SET_PATH = ROOT / "reports" / "eval_set_v3.json"
TOP_K = 10
CAND_K = 30


# ---------------- 指标计算（独立实现） ----------------
def grade_of(rel: str) -> float:
    return 1.0 if rel == "direct" else 0.5


def metrics_for_ranked(ranked_src: list[str], labels: dict[str, str]) -> dict:
    """ranked_src: 按排名排列的 source_file 列表；labels: {source_file: relevance 档位}"""
    direct = {s for s, r in labels.items() if r == "direct"}
    all_rel = set(labels)

    hit1 = 1.0 if ranked_src[:1] and ranked_src[0] in direct else 0.0
    hit5 = 1.0 if any(s in direct for s in ranked_src[:5]) else 0.0

    rr = 0.0
    for rank, s in enumerate(ranked_src, 1):
        if s in direct:
            rr = 1.0 / rank
            break

    rec10 = (len(set(ranked_src[:10]) & all_rel) / len(all_rel)) if all_rel else 0.0

    # NDCG@10：二档增益 direct=1.0, assist=0.5；位置折损 1/log2(rank+1)
    dcg = 0.0
    for rank, s in enumerate(ranked_src[:10], 1):
        if s in labels:
            dcg += grade_of(labels[s]) / _log2(rank + 1)
    ideal = sorted((grade_of(r) for r in labels.values()), reverse=True)
    idcg = sum(g / _log2(i + 2) for i, g in enumerate(ideal))
    ndcg10 = dcg / idcg if idcg > 0 else 0.0

    return {"hit1": hit1, "hit5": hit5, "mrr": rr, "recall10": rec10, "ndcg10": ndcg10}


def _log2(x: float) -> float:
    import math
    return math.log2(x)


def evaluate_config(r: Retriever, items: list[dict], **cfg) -> tuple[dict, list]:
    """跑一遍全部题目，返回 (总体+分层指标矩阵, 每题明细)。"""
    per_item = []
    agg = defaultdict(lambda: defaultdict(list))
    for it in items:
        if it["difficulty"] == "L4":
            continue  # 检索指标分母不含拒答题
        labels = it["labels"]
        if not labels:
            continue
        out = r.search(it["query"], top_k=TOP_K, cand_k=CAND_K, **cfg)
        ranked_src = []
        for c in out["results"]:
            if c.source_file not in ranked_src:  # 文档级去重后再计指标
                ranked_src.append(c.source_file)
        m = metrics_for_ranked(ranked_src, labels)
        m["id"] = it["id"]
        m["difficulty"] = it["difficulty"]
        m["ranked"] = ranked_src[:5]
        per_item.append(m)
        for layer in ("ALL", it["difficulty"]):
            for k, v in m.items():
                if k in ("hit1", "hit5", "mrr", "recall10", "ndcg10"):
                    agg[layer][k].append(v)
    matrix = {}
    for layer, d in agg.items():
        n = len(d["hit1"])
        matrix[layer] = {
            "n": n,
            "hit1": sum(d["hit1"]) / n,
            "hit5": sum(d["hit5"]) / n,
            "mrr": sum(d["mrr"]) / n,
            "recall10": sum(d["recall10"]) / n,
            "ndcg10": sum(d["ndcg10"]) / n,
        }
    return matrix, per_item


CONFIGS = [
    ("A1_纯向量召回", dict(use_bm25=False, use_vector=True, use_rrf=False, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("A2_纯BM25召回", dict(use_bm25=True, use_vector=False, use_rrf=False, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("A3_融合(向量+BM25+RRF)", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("A4_融合+文档级去重", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=True, use_quota=False)),
    ("A5_完整管线(含重排序)", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=True,
                     use_meta_filter=True, use_prior=True, use_dedupe=True, use_quota=True,
                     rerank_cand_k=20)),
]


def main() -> None:
    t_start = time.time()
    es = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))
    items = es["items"]
    r = Retriever()
    print(f"语料块数: {len(r.chunks)}", flush=True)

    results = {"configs": {}}
    detail_all = {}
    for name, cfg in CONFIGS:
        t0 = time.time()
        matrix, per_item = evaluate_config(r, items, **cfg)
        results["configs"][name] = {"matrix": matrix}
        detail_all[name] = per_item
        print(f"\n== {name} (耗时 {time.time()-t0:.0f}s) ==", flush=True)
        for layer in ("ALL", "L1", "L2", "L3"):
            m = matrix.get(layer)
            if m:
                print(f"  {layer}: n={m['n']} Hit@1={m['hit1']:.3f} Hit@5={m['hit5']:.3f} "
                      f"MRR={m['mrr']:.3f} Recall@10={m['recall10']:.3f} NDCG@10={m['ndcg10']:.3f}", flush=True)

    out = {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set_version": es["eval_set_version"],
        "corpus_chunks": len(r.chunks),
        "config_flags": {n: c for n, c in CONFIGS},
        "results": results["configs"],
        "per_item_detail": detail_all,
    }
    (ROOT / "reports" / "retrieval_eval_v3_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n总耗时 {time.time()-t_start:.0f}s，结果已落盘 retrieval_eval_v3_results.json", flush=True)


if __name__ == "__main__":
    main()
