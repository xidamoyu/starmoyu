# -*- coding: utf-8 -*-
"""重构验证：加权融合 + 扩大重排序候选窗口。

五条路线已排除（文档级聚合、类型均衡、短文档惩罚、意图软路由、分词改造），
唯一稳定正向的是「块级 RRF 加权 向量0.5:关键词1.0」（+4.8pt）。

本实验测两个尚未验证的关键杠杆：
  L1 重排序候选窗口：A5 只把前 20 条送进 reranker，而两路 top-30 并集覆盖真值 0.984。
     若窗口是瓶颈，扩大到 30 / 50 应显著提升。
  L2 去掉元数据过滤：隔离实验已证明它净负 −1.2pt、L1 层 −10.0pt。

组合矩阵（全部含重排序，文档级指标，与主评测口径一致）：
  C0 加权融合 + 重排20 + 过滤开（对照，应≈A5 但融合加权）
  C1 加权融合 + 重排20 + 过滤关  ← 只关过滤
  C2 加权融合 + 重排30 + 过滤关  ← 同时扩窗
  C3 加权融合 + 重排50 + 过滤关  ← 更大窗口
  C4 等权融合 + 重排50 + 过滤关  ← 验证加权与扩窗是否独立

用法：.venv/Scripts/python.exe -u scripts/experiment_rerank_window.py
产物：reports/experiment_rerank_window.json
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
OUT = ROOT / "reports" / "experiment_rerank_window.json"
RECALL_DEPTH = 30   # 两路召回深度
TOP_K = 10


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块｜题目 {len(items)} 道", flush=True)

    # (名称, 向量权重, 关键词权重, 重排窗口, 是否元数据过滤)
    cfgs = [
        ("C0_加权+重排20+过滤开", 0.5, 1.0, 20, True),
        ("C1_加权+重排20+过滤关", 0.5, 1.0, 20, False),
        ("C2_加权+重排30+过滤关", 0.5, 1.0, 30, False),
        ("C3_加权+重排50+过滤关", 0.5, 1.0, 50, False),
        ("C4_等权+重排50+过滤关", 1.0, 1.0, 50, False),
    ]
    rows_by_cfg: dict[str, list] = {c[0]: [] for c in cfgs}
    t0 = time.time()

    for n, it in enumerate(items, 1):
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

        for name, wv, wb, win, use_filter in cfgs:
            sc: dict[str, float] = {}
            for lst, w in ((v_chunks, wv), (b_chunks, wb)):
                for rank, cid in enumerate(lst):
                    sc[cid] = sc.get(cid, 0.0) + w / (60 + rank + 1)
            cands = sorted(sc, key=lambda c: -sc[c])

            if use_filter:
                kept, _cond = r.meta_filter(q, cands)
                keep_set = set(kept)
                cands = [c for c in cands if c in keep_set] + [c for c in cands if c not in keep_set]

            # 文档级多样性限流（与主评测同策略：每 doc 前 2 块保序，溢出移队尾）
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

            # 重排序：窗口内送 reranker
            window = cands[:win]
            ordered_all, used = r.rerank(q, window, len(window))
            # 窗口外的候选接在后面（重排只看窗口内，但输出需要满 TOP_K）
            rest = cands[win:]
            ordered = ordered_all + rest

            # 文档级去重 → TOP_K
            seq, seen = [], set()
            for cid in ordered:
                d = doc_of.get(cid)
                if d and d not in seen:
                    seen.add(d)
                    seq.append(d)
                if len(seq) >= TOP_K:
                    break
            m = metrics_for_ranked(seq, it["labels"])
            m["id"] = it["id"]
            m["layer"] = it["difficulty"]
            m["rerank_used"] = used
            rows_by_cfg[name].append(m)

        if n % 20 == 0:
            print(f"  已跑 {n}/{len(items)}（{time.time()-t0:.0f}s）", flush=True)

    results = {}
    for cfg, rows in rows_by_cfg.items():
        agg = {}
        for L in ("ALL", "L1", "L2", "L3"):
            sub = rows if L == "ALL" else [x for x in rows if x["layer"] == L]
            agg[L] = {"n": len(sub)}
            for k in ("hit1", "hit5", "hit10", "mrr", "recall10", "ndcg10"):
                agg[L][k] = round(sum(x[k] for x in sub) / len(sub), 4) if sub else 0.0
        results[cfg] = {"matrix": agg, "per_item": rows,
                        "rerank_fail": sum(1 for x in rows if not x["rerank_used"])}

    OUT.write_text(json.dumps({
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": es["eval_set_version"], "corpus_chunks": len(r.chunks),
        "n_items": len(items), "recall_depth": RECALL_DEPTH,
        "note": "加权融合与重排序窗口的联合验证；全部含重排序",
        "results": {k: {"matrix": v["matrix"], "rerank_fail": v["rerank_fail"]}
                    for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 加权融合 × 重排序窗口 ===")
    for name, *_ in cfgs:
        a = results[name]["matrix"]["ALL"]
        print(f"  {name:<24} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f} NDCG@10={a['ndcg10']:.4f} "
              f"重排失败={results[name]['rerank_fail']}")
    print("\n=== 分层 Hit@1 ===")
    for name, *_ in cfgs:
        m = results[name]["matrix"]
        print(f"  {name:<24} L1={m['L1']['hit1']:.3f} L2={m['L2']['hit1']:.3f} L3={m['L3']['hit1']:.3f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
