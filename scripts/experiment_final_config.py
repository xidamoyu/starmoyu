# -*- coding: utf-8 -*-
"""重构验证（最终）：把已确认有效的两项改动叠加，得到重构后的目标配置。

已确认有效的两项改动互相独立：
  ① 融合加权 向量0.5 : 关键词1.0（等权 → 加权，单独 +4.8pt）
  ② 重排序输入带文档标题（仅正文 → 正文+标题，单独 +2.4pt）

已确认无效或有害、须保持不动的：
  · 重排序窗口 20（扩到 30/50 反而下降，长候选会引入噪声）
  · 元数据过滤（在加权融合基线下无害，净 +2 题）
  · 文档级聚合、类型均衡、短文档惩罚、意图软路由、分词改造（均证伪）

本实验跑最终目标配置，并给出与现状的逐项对照：
  F1_目标配置：加权 + 窗口20 + 过滤开 + 正文+标题
  F2_消融：加权 + 窗口20 + 过滤开 + 仅正文（= C0，用于确认标题的独立贡献）

用法：.venv/Scripts/python.exe -u scripts/experiment_final_config.py
产物：reports/experiment_final_config.json
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
OUT = ROOT / "reports" / "experiment_final_config.json"
RECALL_DEPTH = 30
WINDOW = 20
TOP_K = 10


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块｜题目 {len(items)} 道｜窗口 {WINDOW}", flush=True)

    cfgs = ["F1_目标配置_正文+标题", "F2_消融_仅正文"]
    rows_by_cfg: dict[str, list] = {c: [] for c in cfgs}
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

        for cfg in cfgs:
            if cfg == "F1_目标配置_正文+标题":
                docs = [f"【{r.by_id[c].doc_title}】{r.by_id[c].content}"[:2000] for c in window]
            else:
                docs = [r.by_id[c].content[:2000] for c in window]
            try:
                ranked = llm.rerank(q, docs, top_n=None)
                ordered = [window[i] for i, _ in ranked if 0 <= i < len(window)]
                ordered += [c for c in window if c not in set(ordered)]
                used = True
            except Exception:
                ordered = window
                used = False
            ordered += cands[WINDOW:]

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
            rows_by_cfg[cfg].append(m)

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
        "n_items": len(items), "recall_depth": RECALL_DEPTH, "window": WINDOW,
        "note": "重构后的目标配置与消融对照；窗口20、含重排序",
        "results": {k: {"matrix": v["matrix"], "rerank_fail": v["rerank_fail"]}
                    for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 目标配置 vs 消融（窗口 20，含重排序）===")
    for cfg in cfgs:
        a = results[cfg]["matrix"]["ALL"]
        print(f"  {cfg:<24} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f} NDCG@10={a['ndcg10']:.4f} "
              f"失败={results[cfg]['rerank_fail']}")
    print("\n=== 分层 Hit@1 ===")
    for cfg in cfgs:
        m = results[cfg]["matrix"]
        print(f"  {cfg:<24} L1={m['L1']['hit1']:.3f} L2={m['L2']['hit1']:.3f} L3={m['L3']['hit1']:.3f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
