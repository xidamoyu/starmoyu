# -*- coding: utf-8 -*-
"""隔离实验：把 A5 相对 A4 的增益拆到各开关上。

主评测里 A5 相对 A4 同时改了四个开关（重排序 / 元数据过滤 / 领域先验 / 配额），
因此「重排序贡献 +25.6 个百分点」这种归因是不成立的。本脚本只做一件事：
在 A4 的基线上，逐个单独打开每个开关，测各自的净贡献。

用法：.venv/Scripts/python.exe -u scripts/experiment_a5_attribution.py
产物：reports/experiment_a5_attribution.json / .log
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from starmoyu.retriever import Retriever  # noqa: E402

EVAL = ROOT / "reports" / "eval_set_v6.json"
OUT = ROOT / "reports" / "experiment_a5_attribution.json"
TOP_K, CAND_K = 10, 30

BASE = dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False,
            use_meta_filter=False, use_prior=False, use_dedupe=True, use_quota=False)

CONFIGS = [
    ("A4_基线(融合+去重)", dict(BASE)),
    ("A4+仅重排序", {**BASE, "use_rerank": True, "rerank_cand_k": 20}),
    ("A4+仅元数据过滤", {**BASE, "use_meta_filter": True}),
    ("A4+仅领域先验", {**BASE, "use_prior": True}),
    ("A4+仅配额", {**BASE, "use_quota": True}),
    ("A4+过滤+先验+配额(无重排)", {**BASE, "use_meta_filter": True, "use_prior": True, "use_quota": True}),
    ("A5_完整管线", {**BASE, "use_rerank": True, "rerank_cand_k": 20,
                     "use_meta_filter": True, "use_prior": True, "use_quota": True}),
]


def rank_docs(out: dict, doc_of: dict) -> list[str]:
    """块级结果 → 文档级去重排名序列（与主评测口径一致）。"""
    seq: list[str] = []
    for c in out["results"]:
        d = doc_of.get(c.chunk_id)
        if d and d not in seq:
            seq.append(d)
    return seq


# 指标实现直接复用主评测脚本，禁止另写一套口径
sys.path.insert(0, str(ROOT / "scripts"))
from eval_v5_retrieval import metrics_for_ranked  # noqa: E402


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块｜题目 {len(items)} 道", flush=True)

    results: dict[str, dict] = {}
    detail: dict[str, list] = {}
    for name, cfg in CONFIGS:
        t0 = time.time()
        rows = []
        for it in items:
            out = r.search(it["query"], top_k=TOP_K, cand_k=CAND_K, **cfg)
            seq = rank_docs(out, doc_of)
            m = metrics_for_ranked(seq, it["labels"])
            m["id"] = it["id"]
            m["layer"] = it["difficulty"]
            rows.append(m)
        agg = {}
        for L in ("ALL", "L1", "L2", "L3"):
            sub = rows if L == "ALL" else [x for x in rows if x["layer"] == L]
            agg[L] = {"n": len(sub)}
            for k in ("hit1", "hit5", "hit10", "mrr", "recall10", "ndcg10"):
                agg[L][k] = round(sum(x[k] for x in sub) / len(sub), 4) if sub else 0.0
        results[name] = {"matrix": agg, "elapsed_s": round(time.time() - t0, 1)}
        detail[name] = rows
        a = agg["ALL"]
        print(f"  {name:<30} 耗时 {results[name]['elapsed_s']:>6.1f}s  "
              f"Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f} NDCG@10={a['ndcg10']:.4f}", flush=True)

    OUT.write_text(json.dumps({
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": es["eval_set_version"], "corpus_chunks": len(r.chunks),
        "n_items": len(items),
        "note": "在 A4 基线上逐个单独打开 A5 的四个开关，用于把 A5 的增益拆到各开关",
        "results": results, "per_item_detail": detail,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已落盘 {OUT.name}", flush=True)


if __name__ == "__main__":
    main()
