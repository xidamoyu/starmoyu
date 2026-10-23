# -*- coding: utf-8 -*-
"""重构验证：重排序的输入构造（是否给模型文档标题与章节）。

现状缺陷：`llm.rerank` 收到的文档只有 `chunk.content[:2000]`，
`doc_title` 与 `section` 全部丢弃。对「XX 的合作评级是什么」这类问句，
块正文是一行表格数据（`| K00123 | @小羊同学 | 腰部 | 132.0w | ... |`），
重排模型无法从这一行判断它属于「生活家居类目达人刊例表」，
相关性判断只能靠字面巧合。

本实验对比三种重排输入构造（召回与融合固定为「块级 RRF 加权 0.5:1.0 + 去重」）：
  H0 仅正文（现状）
  H1 正文 + 文档标题（【标题】正文）
  H2 正文 + 文档标题 + 章节（【标题 / 章节】正文）

用法：.venv/Scripts/python.exe -u scripts/experiment_rerank_input.py
产物：reports/experiment_rerank_input.json
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
OUT = ROOT / "reports" / "experiment_rerank_input.json"
RECALL_DEPTH = 30
TOP_K = 10
WINDOW = 30


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块｜题目 {len(items)} 道", flush=True)

    cfgs = ["H0_仅正文", "H1_正文+标题", "H2_正文+标题+章节"]
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

        # 文档级多样性限流
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
            if cfg == "H0_仅正文":
                docs = [r.by_id[c].content[:2000] for c in window]
            elif cfg == "H1_正文+标题":
                docs = [f"【{r.by_id[c].doc_title}】{r.by_id[c].content}"[:2000] for c in window]
            else:
                docs = [f"【{r.by_id[c].doc_title} / {r.by_id[c].section}】{r.by_id[c].content}"[:2000]
                        for c in window]
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
        "note": "重排序输入构造对比；召回与融合固定为加权 RRF + 去重",
        "results": {k: {"matrix": v["matrix"], "rerank_fail": v["rerank_fail"]}
                    for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 重排序输入构造对比（窗口 30）===")
    for cfg in cfgs:
        a = results[cfg]["matrix"]["ALL"]
        print(f"  {cfg:<18} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f} NDCG@10={a['ndcg10']:.4f} "
              f"失败={results[cfg]['rerank_fail']}")
    print("\n=== 分层 Hit@1 ===")
    for cfg in cfgs:
        m = results[cfg]["matrix"]
        print(f"  {cfg:<18} L1={m['L1']['hit1']:.3f} L2={m['L2']['hit1']:.3f} L3={m['L3']['hit1']:.3f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
