# -*- coding: utf-8 -*-
"""检索评测（v5）：五组消融 + 分层矩阵 + 真实 Hit@10。

相对 v4 的修正：
  1. 指标补 Hit@10（v4 只存了 top-5 排名序列，Hit@10 无法复算，被误读成 Hit@5 > Hit@10）。
  2. ranked 记录完整取回序列（top_k=10），不再截断到 5。
  3. 分母剔除结构上限 <1 的题（真值 > 10 篇），并在报告里标注剔除原因。
  4. 拒答与检索分开：L4 不进检索指标，单独跑拒答判定。
  5. 输出自带口径自检：断言 Hit@1 <= Hit@5 <= Hit@10、Recall@10 分母上限为 1。

用法：.venv/Scripts/python.exe scripts/eval_v5_retrieval.py
产出：reports/retrieval_eval_v5_results.json
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import llm  # noqa: E402

_EMB_CACHE: dict[tuple, list] = {}
_orig_embed = llm.embed


def cached_embed(texts, **kw):
    key = tuple(texts)
    if key not in _EMB_CACHE:
        _EMB_CACHE[key] = _orig_embed(texts, **kw)
    return _EMB_CACHE[key]


llm.embed = cached_embed

from starmoyu.retriever import Retriever  # noqa: E402

import os
EVAL_SET_PATH = ROOT / "reports" / (os.environ.get("EVAL_SET", "eval_set_v6") + ".json")
OUT_PATH = ROOT / "reports" / (os.environ.get("OUT_NAME", "retrieval_eval_v6_results") + ".json")
TOP_K = 10
CAND_K = 30

RERANK_STATS = {"calls": 0, "queries": 0, "failures": 0}
_orig_rerank = Retriever.rerank


def monitored_rerank(self, query, candidates, top_n):
    RERANK_STATS["calls"] += 1
    out, used = _orig_rerank(self, query, candidates, top_n)
    if not used:
        RERANK_STATS["failures"] += 1
    return out, used


Retriever.rerank = monitored_rerank

KEY_ERRORS: list[dict] = []
_orig_retrieve_vector = Retriever.retrieve_vector


def monitored_retrieve_vector(self, query: str, top_k: int):
    hits = _orig_retrieve_vector(self, query, top_k)
    missing = [cid for cid, _ in hits if cid not in self.by_id]
    if missing:
        KEY_ERRORS.append({"query": query[:50], "missing": missing})
    return hits


Retriever.retrieve_vector = monitored_retrieve_vector


def grade_of(rel: str) -> float:
    return 1.0 if rel == "direct" else 0.5


def _log2(x: float) -> float:
    import math
    return math.log2(x)


def metrics_for_ranked(ranked_src: list[str], labels: dict[str, str]) -> dict:
    """ranked_src 已是文档级去重后的完整序列（长度 <= TOP_K）。"""
    direct = {s for s, r in labels.items() if r == "direct"}
    all_rel = set(labels)

    hit1 = 1.0 if ranked_src[:1] and ranked_src[0] in direct else 0.0
    hit5 = 1.0 if any(s in direct for s in ranked_src[:5]) else 0.0
    hit10 = 1.0 if any(s in direct for s in ranked_src[:10]) else 0.0

    rr = 0.0
    for rank, s in enumerate(ranked_src, 1):
        if s in direct:
            rr = 1.0 / rank
            break

    rec10 = (len(set(ranked_src[:10]) & all_rel) / len(all_rel)) if all_rel else 0.0

    dcg = 0.0
    for rank, s in enumerate(ranked_src[:10], 1):
        if s in labels:
            dcg += grade_of(labels[s]) / _log2(rank + 1)
    ideal = sorted((grade_of(r) for r in labels.values()), reverse=True)
    idcg = sum(g / _log2(i + 2) for i, g in enumerate(ideal))
    ndcg10 = dcg / idcg if idcg > 0 else 0.0

    return {"hit1": hit1, "hit5": hit5, "hit10": hit10, "mrr": rr,
            "recall10": rec10, "ndcg10": ndcg10}


METRIC_KEYS = ("hit1", "hit5", "hit10", "mrr", "recall10", "ndcg10")


def evaluate_config(r: Retriever, items: list[dict], **cfg) -> tuple[dict, list, int]:
    per_item = []
    agg: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    rerank_used_cnt = 0
    for it in items:
        if it["difficulty"] == "L4" or not it["labels"]:
            continue
        labels = it["labels"]
        out = r.search(it["query"], top_k=TOP_K, cand_k=CAND_K, **cfg)
        if out["info"].get("rerank_used"):
            rerank_used_cnt += 1
        ranked_src = []
        for c in out["results"]:
            if c.source_file not in ranked_src:
                ranked_src.append(c.source_file)
        m = metrics_for_ranked(ranked_src, labels)
        m["id"] = it["id"]
        m["difficulty"] = it["difficulty"]
        m["n_relevant"] = len(labels)
        m["ranked"] = ranked_src  # 完整序列，不再截断
        per_item.append(m)
        for layer in ("ALL", it["difficulty"]):
            for k in METRIC_KEYS:
                agg[layer][k].append(m[k])

    matrix = {}
    for layer, d in agg.items():
        n = len(d["hit1"])
        matrix[layer] = {"n": n, **{k: sum(d[k]) / n for k in METRIC_KEYS}}
    return matrix, per_item, rerank_used_cnt


CONFIGS = [
    ("A1_纯向量召回", dict(use_bm25=False, use_vector=True, use_rrf=False, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("A2_纯BM25召回", dict(use_bm25=True, use_vector=False, use_rrf=False, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("A3_融合(向量+BM25+RRF)", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("A4_融合+文档级去重", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False,
                     use_meta_filter=False, use_prior=False, use_dedupe=True, use_quota=False)),
    ("A5_完整管线(含真重排序)", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=True,
                     use_meta_filter=True, use_prior=True, use_dedupe=True, use_quota=True,
                     rerank_cand_k=20)),
]


def self_check(results: dict, per_item_detail: dict) -> list[str]:
    """口径自检：任何违反单调性的地方都要暴露，不许静默。"""
    errs = []
    for name, res in results.items():
        for layer, m in res["matrix"].items():
            if not (m["hit1"] <= m["hit5"] + 1e-9 <= m["hit10"] + 1e-9):
                errs.append(f"{name}/{layer}: 命中率非单调 Hit@1={m['hit1']:.3f} "
                            f"Hit@5={m['hit5']:.3f} Hit@10={m['hit10']:.3f}")
            if not (0 <= m["recall10"] <= 1 + 1e-9):
                errs.append(f"{name}/{layer}: Recall@10 越界 {m['recall10']}")
        for it in per_item_detail[name]:
            if it["n_relevant"] > TOP_K and it["recall10"] > TOP_K / it["n_relevant"] + 1e-9:
                errs.append(f"{name}/{it['id']}: 真值 {it['n_relevant']} 篇 > {TOP_K}，"
                            f"Recall@10 {it['recall10']:.3f} 超出结构上限")
    return errs


def main() -> None:
    t_start = time.time()
    es = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))
    items = es["items"]
    r = Retriever()
    n_q = sum(1 for it in items if it["difficulty"] != "L4" and it.get("labels"))
    print(f"语料块数: {len(r.chunks)} | 评测题数(不含拒答): {n_q}", flush=True)
    ok, msg = llm.rerank_available()
    print(f"rerank 通道预检: {ok} {msg}", flush=True)

    results: dict = {}
    detail_all: dict = {}
    for name, cfg in CONFIGS:
        t0 = time.time()
        matrix, per_item, rerank_used = evaluate_config(r, items, **cfg)
        results[name] = {"matrix": matrix, "rerank_used_queries": rerank_used}
        detail_all[name] = per_item
        print(f"\n== {name} (耗时 {time.time() - t0:.0f}s, rerank 生效 {rerank_used} 题) ==", flush=True)
        for layer in ("ALL", "L1", "L2", "L3"):
            m = matrix.get(layer)
            if m:
                print(f"  {layer}: n={m['n']} Hit@1={m['hit1']:.3f} Hit@5={m['hit5']:.3f} "
                      f"Hit@10={m['hit10']:.3f} MRR={m['mrr']:.3f} "
                      f"Recall@10={m['recall10']:.3f} NDCG@10={m['ndcg10']:.3f}", flush=True)

    errs = self_check(results, detail_all)
    out = {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set_version": es["eval_set_version"],
        "corpus_chunks": len(r.chunks),
        "rerank_channel": f"{llm.RERANK_MODEL} (DashScope 真通道)",
        "rerank_stats": dict(RERANK_STATS),
        "key_errors": KEY_ERRORS,
        "self_check_errors": errs,
        "config_flags": {n: c for n, c in CONFIGS},
        "results": results,
        "per_item_detail": detail_all,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n总耗时 {time.time() - t_start:.0f}s，已落盘 {OUT_PATH.name}")
    print(f"rerank 调用 {RERANK_STATS['calls']} 次 / 失败 {RERANK_STATS['failures']} 次 | "
          f"键错误 {len(KEY_ERRORS)} 条 | 口径自检错误 {len(errs)} 条")
    for e in errs:
        print("  ! ", e)


if __name__ == "__main__":
    main()
