# -*- coding: utf-8 -*-
"""重构方向验证：打分函数与文档失衡的修正。

诊断已确认三个结构性缺陷：
  F1 语料严重失衡：inflight_deal 975 块（54%）、deal_case 451、rate_card 360，
     而 platform_policy 只有 11 块、methodology 7 块。用词高度同构的跟踪单
     在 BM25 里靠词频积累压过篇数少的政策、方法论。
  F2 杂项文档污染：imports/ 下上传的零散笔记 doc_type=other，5 个短块，
     业务词密度极高，在 BM25 的短文档偏置下稳定抢占第 1 名。
  F3 文档级可召回但排序错：BM25 文档级 top-30 覆盖 0.980、top-1 仅 0.628，
     92 道排错题里 62 道只差 2 至 5 名。

本实验测三条修正路径，全部在文档级输出：
  S0 基线：块级 RRF 等权（现状）
  S1 类型均衡加权：对候选分按 doc_type 的语料份额做倒数加权，
     压制占 54% 的 inflight_deal、抬升只有 11 块的政策
  S2 短文档惩罚：对内容长度远低于中位数的块做长度惩罚（压制 imports 杂项）
  S3 S1+S2 合并
  S4 S1+S2 + 文档级聚合（取文档最好块）

用法：.venv/Scripts/python.exe -u scripts/experiment_score_fix.py
产物：reports/experiment_score_fix.json
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter, defaultdict
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
OUT = ROOT / "reports" / "experiment_score_fix.json"
DEPTH = 100
TOP_K = 10


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    doctype = {c.chunk_id: c.doc_type for c in r.chunks}
    length = {c.chunk_id: len(c.content) for c in r.chunks}

    type_cnt = Counter(doctype.values())
    total = sum(type_cnt.values())
    share = {t: n / total for t, n in type_cnt.items()}
    med_len = statistics.median(length.values())
    print(f"语料 {len(r.chunks)} 块 / {len(set(doc_of.values()))} 篇｜题目 {len(items)} 道", flush=True)
    print(f"类型份额：{ {t: round(s,3) for t,s in share.items()} }", flush=True)
    print(f"块长度中位数 {med_len:.0f} 字", flush=True)

    # 类型倒数权重：份额越低权重越高，中位类型权重为 1
    med_share = statistics.median(share.values())
    type_w = {t: (med_share / s) ** 0.5 for t, s in share.items()}  # 开方抑制极端值
    print(f"类型权重：{ {t: round(w,2) for t,w in type_w.items()} }", flush=True)

    cfgs = ["S0_基线块级RRF", "S1_类型均衡", "S2_短文档惩罚", "S3_类型均衡+短文档惩罚", "S4_S3+文档级聚合"]
    rows_by_cfg: dict[str, list] = {c: [] for c in cfgs}
    t0 = time.time()

    for n, it in enumerate(items, 1):
        q = it["query"]
        raw_v = r.retrieve_vector(q, DEPTH)
        raw_b = r.retrieve_bm25(q, DEPTH)

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

        def block_rrf() -> dict[str, float]:
            sc: dict[str, float] = {}
            for lst in (v_chunks, b_chunks):
                for rank, cid in enumerate(lst):
                    sc[cid] = sc.get(cid, 0.0) + 1.0 / (60 + rank + 1)
            return sc

        base = block_rrf()

        def s1_apply(sc: dict[str, float]) -> dict[str, float]:
            return {c: s * type_w.get(doctype.get(c, "other"), 1.0) for c, s in sc.items()}

        def s2_apply(sc: dict[str, float]) -> dict[str, float]:
            out = {}
            for c, s in sc.items():
                L = length.get(c, med_len)
                # 长度惩罚：短于中位数 60% 的块按比例降权，下限 0.3
                factor = min(1.0, max(0.3, (L / med_len) ** 0.5)) if L < med_len else 1.0
                out[c] = s * factor
            return out

        sc_s1 = s1_apply(base)
        sc_s2 = s2_apply(base)
        sc_s3 = s2_apply(s1_apply(base))

        def to_docs(sc: dict[str, float]) -> list[str]:
            seq, seen = [], set()
            for cid in sorted(sc, key=lambda c: -sc[c]):
                d = doc_of.get(cid)
                if d and d not in seen:
                    seen.add(d)
                    seq.append(d)
            return seq[:TOP_K]

        def to_docs_agg(sc: dict[str, float]) -> list[str]:
            """文档级：取该文档最好块的分作为文档分。"""
            best: dict[str, float] = {}
            for cid, s in sc.items():
                d = doc_of.get(cid)
                if d:
                    best[d] = max(best.get(d, -1.0), s)
            return sorted(best, key=lambda d: -best[d])[:TOP_K]

        ranked = {
            "S0_基线块级RRF": to_docs(base),
            "S1_类型均衡": to_docs(sc_s1),
            "S2_短文档惩罚": to_docs(sc_s2),
            "S3_类型均衡+短文档惩罚": to_docs(sc_s3),
            "S4_S3+文档级聚合": to_docs_agg(sc_s3),
        }
        for cfg in cfgs:
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
        "type_share": share, "type_weight": type_w, "median_block_len": med_len,
        "note": "类型均衡加权与短文档惩罚的修正效果；不含重排序",
        "results": {k: {"matrix": v["matrix"]} for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 打分修正对比（不含重排序）===")
    for cfg in cfgs:
        a = results[cfg]["matrix"]["ALL"]
        print(f"  {cfg:<24} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f} NDCG@10={a['ndcg10']:.4f}")
    print("\n=== 分层 Hit@1 ===")
    for cfg in cfgs:
        m = results[cfg]["matrix"]
        print(f"  {cfg:<24} L1={m['L1']['hit1']:.3f} L2={m['L2']['hit1']:.3f} L3={m['L3']['hit1']:.3f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
