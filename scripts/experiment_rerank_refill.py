# -*- coding: utf-8 -*-
"""补跑：补齐 experiment_rerank_window 里因 API 配额耗尽而未生效重排的题目。

现象：C0 配置的 250 题里有 20 题 rerank_used=False，且全部集中在序列尾部
（下标 228 起连续到底），是 DashScope 单次配额耗尽的典型特征，不是代码缺陷。
这 20 题的 Hit@1 只有 0.600（全靠融合排序），拉低了整体成绩，因此 0.748 是保守下限。

本脚本只对这 20 题重跑重排，其余 230 题的成绩从原结果文件直接复用，避免重复消耗配额。

用法：.venv/Scripts/python.exe -u scripts/experiment_rerank_refill.py
产物：reports/experiment_rerank_window_refilled.json
"""
from __future__ import annotations

import json
import sys
import time
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
SRC = ROOT / "reports" / "experiment_rerank_window.json"
OUT = ROOT / "reports" / "experiment_rerank_window_refilled.json"
CFG = "C0_加权+重排20+过滤开"
RECALL_DEPTH = 30
WINDOW = 20
TOP_K = 10


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    by_id = {i["id"]: i for i in es["items"]}
    src = json.loads(SRC.read_text(encoding="utf-8"))
    rows = src["per_item_detail"][CFG]
    todo = [x["id"] for x in rows if not x["rerank_used"]]
    print(f"需补跑 {len(todo)} 题：{todo[0]} … {todo[-1]}", flush=True)

    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    fixed: dict[str, dict] = {}
    t0 = time.time()

    for i, qid in enumerate(todo, 1):
        it = by_id[qid]
        q = it["query"]
        raw_v = r.retrieve_vector(q, RECALL_DEPTH)
        raw_b = r.retrieve_bm25(q, RECALL_DEPTH)

        def norm(hits):
            if not hits:
                return {}
            vals = [s for _, s in hits]
            lo, hi = min(vals), max(vals)
            span = (hi - lo) or 1.0
            return {c: (s - lo) / span for c, s in hits}

        v_sc, b_sc = norm(raw_v), norm(raw_b)
        v_chunks = sorted(v_sc, key=lambda c: -v_sc[c])
        b_chunks = sorted(b_sc, key=lambda c: -b_sc[c])

        sc: dict[str, float] = {}
        for lst, w in ((v_chunks, 0.5), (b_chunks, 1.0)):
            for rank, cid in enumerate(lst):
                sc[cid] = sc.get(cid, 0.0) + w / (60 + rank + 1)
        cands = sorted(sc, key=lambda c: -sc[c])

        kept, _cond = r.meta_filter(q, cands)
        keep_set = set(kept)
        cands = [c for c in cands if c in keep_set] + [c for c in cands if c not in keep_set]

        cnt: dict[str, int] = {}
        keep, overflow = [], []
        for cid in cands:
            c = r.by_id.get(cid)
            if c is None:
                continue
            if cnt.get(c.doc_id, 0) < 2:
                cnt[c.doc_id] = cnt.get(c.doc_id, 0) + 1
                keep.append(cid)
            else:
                overflow.append(cid)
        cands = keep + overflow

        window = cands[:WINDOW]
        ordered_all, used = r.rerank(q, window, len(window))
        ordered = ordered_all + cands[WINDOW:]

        seq, seen = [], set()
        for cid in ordered:
            d = doc_of.get(cid)
            if d and d not in seen:
                seen.add(d)
                seq.append(d)
            if len(seq) >= TOP_K:
                break
        m = metrics_for_ranked(seq, it["labels"])
        m["id"] = qid
        m["layer"] = it["difficulty"]
        m["rerank_used"] = used
        fixed[qid] = m
        print(f"  [{i}/{len(todo)}] {qid} used={used} hit1={m['hit1']:.0f} "
              f"（{time.time()-t0:.0f}s）", flush=True)
        time.sleep(0.3)

    # 合并：原成绩里成功重排的题原样保留，失败题用补跑结果替换
    merged = []
    for x in rows:
        merged.append(fixed.get(x["id"], x))

    agg = {}
    for L in ("ALL", "L1", "L2", "L3"):
        sub = merged if L == "ALL" else [x for x in merged if x["layer"] == L]
        agg[L] = {"n": len(sub)}
        for k in ("hit1", "hit5", "hit10", "mrr", "recall10", "ndcg10"):
            agg[L][k] = round(sum(x[k] for x in sub) / len(sub), 4) if sub else 0.0

    still = sum(1 for x in merged if not x["rerank_used"])
    save = {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": es["eval_set_version"], "corpus_chunks": len(r.chunks),
        "n_items": len(merged), "config": CFG,
        "note": "补齐因 API 配额耗尽而未生效重排的题目；其余题目成绩原样复用",
        "refilled_ids": todo, "still_failed": still,
        "matrix": agg,
        "per_item_detail": merged,
    }
    OUT.write_text(json.dumps(save, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n=== 补齐后（{CFG}，仍失败 {still} 题）===")
    for L in ("ALL", "L1", "L2", "L3"):
        m = agg[L]
        print(f"  {L:<4} n={m['n']:3d} Hit@1={m['hit1']:.4f} Hit@5={m['hit5']:.4f} "
              f"MRR={m['mrr']:.4f} R@10={m['recall10']:.4f} NDCG@10={m['ndcg10']:.4f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
