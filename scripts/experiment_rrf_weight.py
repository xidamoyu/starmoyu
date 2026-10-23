# -*- coding: utf-8 -*-
"""验证实验：RRF 等权融合是否是 v6 题库上 Hit@1 偏低的根因。

假设：Milvus 向量路在「专有名词查询」（达人昵称）上几乎没有信号（A1 实测 Hit@1=0.125），
      而 BM25 字面匹配天然擅长。RRF 用 1/(k+rank) 等权相加两路，等于把一路的噪声
      按同等权重混进另一路的好排名里 —— 观察到 A3 相对 A2 净丢 51 题，其中昵称题
      丢 38 题、救回 3 题。

做法：给两路各加一个权重 w_vec / w_bm，跑网格搜索。只改融合打分，不动其他的。
      如果 w_vec < 1 明显更好，说明等权是缺陷；如果 1:1 已是最优，说明假设不成立。

用法：.venv/Scripts/python.exe scripts/experiment_rrf_weight.py
产出：reports/experiment_rrf_weight.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import llm  # noqa: E402

_CACHE: dict[tuple, list] = {}
_orig = llm.embed


def cached(texts, **kw):
    key = tuple(texts)
    if key not in _CACHE:
        _CACHE[key] = _orig(texts, **kw)
    return _CACHE[key]


llm.embed = cached

from starmoyu.retriever import Retriever  # noqa: E402

EVAL = ROOT / "reports" / "eval_set_v6.json"
OUT = ROOT / "reports" / "experiment_rrf_weight.json"


def weighted_rrf(rank_lists, weights, k=60):
    """与 Retriever.rrf 同构，只是每一路带权重。"""
    score: dict[str, float] = {}
    for rl, w in zip(rank_lists, weights):
        for rank, cid in enumerate(rl):
            score[cid] = score.get(cid, 0.0) + w * (1.0 / (k + rank + 1))
    return score


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    print(f"语料 {len(r.chunks)} 块｜题目 {len(items)} 道", flush=True)

    # 先取两路原始排名（依赖 search 内部打分，这里用只开单路的配置拿到干净排名）
    t0 = time.time()
    vec_rank: dict[str, list[str]] = {}
    bm_rank: dict[str, list[str]] = {}
    for it in items:
        out = r.search(it["query"], top_k=10, cand_k=30, use_bm25=False, use_vector=True,
                       use_rrf=False, use_rerank=False, use_meta_filter=False, use_prior=False,
                       use_dedupe=False, use_quota=False)
        vec_rank[it["id"]] = [c.chunk_id for c in out["results"]]
        out = r.search(it["query"], top_k=10, cand_k=30, use_bm25=True, use_vector=False,
                       use_rrf=False, use_rerank=False, use_meta_filter=False, use_prior=False,
                       use_dedupe=False, use_quota=False)
        bm_rank[it["id"]] = [c.chunk_id for c in out["results"]]
    print(f"两路排名采集完成 {time.time() - t0:.0f}s", flush=True)

    # 块 ID → 文档名，指标按文档级算（与主评测一致）
    doc = {c.chunk_id: c.source_file for c in r.chunks}

    def hit1_of(weights) -> tuple[float, float]:
        hit1 = hit5 = 0
        for it in items:
            fused = weighted_rrf([vec_rank[it["id"]], bm_rank[it["id"]]], weights)
            cands = sorted(fused, key=lambda c: -fused[c])
            ranked_docs: list[str] = []
            for cid in cands:
                d = doc.get(cid)
                if d and d not in ranked_docs:
                    ranked_docs.append(d)
            direct = {f for f, v in it["labels"].items() if v == "direct"}
            if ranked_docs[:1] and ranked_docs[0] in direct:
                hit1 += 1
            if any(d in direct for d in ranked_docs[:5]):
                hit5 += 1
        n = len(items)
        return hit1 / n, hit5 / n

    grid = [(1.0, 1.0), (0.5, 1.0), (0.3, 1.0), (0.2, 1.0), (0.1, 1.0),
            (0.0, 1.0), (1.0, 0.5), (1.0, 2.0), (0.5, 1.5), (0.3, 1.5)]
    rows = []
    for w in grid:
        h1, h5 = hit1_of(w)
        rows.append({"w_vec": w[0], "w_bm25": w[1], "hit1": round(h1, 4), "hit5": round(h5, 4)})
        print(f"  w_vec={w[0]:<4} w_bm25={w[1]:<4}  Hit@1={h1:.4f}  Hit@5={h5:.4f}", flush=True)

    best = max(rows, key=lambda x: (x["hit1"], x["hit5"]))
    equal = next(x for x in rows if x["w_vec"] == 1.0 and x["w_bm25"] == 1.0)
    OUT.write_text(json.dumps({
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": es["eval_set_version"], "n_items": len(items),
        "note": "等权 RRF vs 加权 RRF 的网格搜索；指标为文档级 Hit@1/Hit@5，不含重排",
        "rows": rows, "best": best, "equal_weight": equal,
        "delta_hit1": round(best["hit1"] - equal["hit1"], 4),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n最优: {best}")
    print(f"等权基线: {equal}")
    print(f"加权收益 Hit@1: {best['hit1'] - equal['hit1']:+.4f}")
    print(f"已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
