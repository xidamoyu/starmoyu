# -*- coding: utf-8 -*-
"""重构方向验证：查询意图分流（query routing）。

诊断结论：两路 top-30 并集覆盖真值 0.984，但任一路 top-1 命中仅 0.708 ——
瓶颈是排序而非召回。而当前 doc_type_prior 只认 6 组关键词，L1 昵称题问
「粉丝多少 / CPM 多少 / 评级是什么」时先验完全不触发，这是 L1 掉到 0.629 的直接原因。

本实验验证「按查询意图分通道」能到多少：
  R0 现状：块级 RRF 等权（基线）
  R1 全通道 RRF 加权 0.5:1（关键词优先）
  R2 意图路由：识别查询问的是「达人属性 / 案例 / 政策 / 方法论」，
     按 intent 只走向对应 doc_type 加权后的融合分（软路由，不做硬过滤）
  R3 意图路由 + 文档级聚合（软路由 + 取文档最好块）

用法：.venv/Scripts/python.exe -u scripts/experiment_intent_routing.py
产物：reports/experiment_intent_routing.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
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
OUT = ROOT / "reports" / "experiment_intent_routing.json"
DEPTH = 100
TOP_K = 10

# 达人属性问句：问某个达人的粉丝/报价/CPM/评级/播放量等，答案在刊例表里
NICK_ATTR = re.compile(r"粉丝|量级|档位|CPM|cpm|播放量|互动率|评级|刊例|报价|报多少|多少钱|价格|单价")
# 案例/交付问句
CASE_Q = re.compile(r"做过|案例|交付|复盘|ROI|roi|结案|投放记录|合作记录|项目|单子|商单|丢过|在跑|跑完")
# 政策/规则问句
POLICY_Q = re.compile(r"规则|政策|合规|红线|资质|流程|分界线|提前多久|能不能|可以吗|要求|标准|SOP|违规|处罚")
# 方法论问句
METH_Q = re.compile(r"怎么选|策略|方法论|方法|技巧|框架|建议|怎么分配|如何|怎么谈")


def intent_of(q: str) -> tuple[str, dict[str, float]]:
    """返回 (意图名, doc_type 权重表)。多意图并存时按优先级取最强。"""
    scores = {}
    if NICK_ATTR.search(q):
        scores["rate_card"] = 2.0
    if CASE_Q.search(q):
        scores["deal_case"] = 2.0
        scores["inflight_deal"] = 0.8
    if POLICY_Q.search(q):
        scores["platform_policy"] = 2.0
        scores["methodology"] = 0.6
    if METH_Q.search(q):
        scores["methodology"] = 1.8
        scores["platform_policy"] = 0.6
    if not scores:
        return "未知", {}
    return max(scores.items(), key=lambda kv: kv[1])[0], scores


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    doctype = {c.chunk_id: c.doc_type for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块 / {len(set(doc_of.values()))} 篇｜题目 {len(items)} 道", flush=True)

    # 意图分布（先看路由是否覆盖得住）
    from collections import Counter
    ic = Counter(intent_of(i["query"])[0] for i in items)
    print(f"意图分布：{dict(ic)}", flush=True)

    cfgs = ["R0_块级RRF等权", "R1_块级RRF加权0.5:1", "R2_意图软路由_块级", "R3_意图软路由_文档级"]
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

        def block_rrf(wv: float, wb: float) -> dict[str, float]:
            sc: dict[str, float] = {}
            for lst, w in ((v_chunks, wv), (b_chunks, wb)):
                for rank, cid in enumerate(lst):
                    sc[cid] = sc.get(cid, 0.0) + w / (60 + rank + 1)
            return sc

        _, prior = intent_of(q)

        def apply_prior(sc: dict[str, float], strength: float) -> dict[str, float]:
            """软路由：按 doc_type 权重乘性调节块分，不是硬过滤。"""
            if not prior:
                return sc
            out = {}
            for cid, s in sc.items():
                w = prior.get(doctype.get(cid, ""), 0.35)
                # strength 控制先验压制力度：1.0 = 原样乘，0.0 = 不生效
                out[cid] = s * (1.0 + strength * (w - 1.0))
            return out

        r0 = block_rrf(1.0, 1.0)
        r1 = block_rrf(0.5, 1.0)
        r2 = apply_prior(block_rrf(1.0, 1.0), 0.6)

        def chunks_to_docs(sc: dict[str, float]) -> list[str]:
            seq, seen = [], set()
            for cid in sorted(sc, key=lambda c: -sc[c]):
                d = doc_of.get(cid)
                if d and d not in seen:
                    seen.add(d)
                    seq.append(d)
            return seq[:TOP_K]

        # R3：文档级聚合 + 意图软路由
        def doc_level(wv: float, wb: float, strength: float) -> list[str]:
            doc_sc: dict[str, float] = defaultdict(float)
            best: dict[str, float] = defaultdict(float)
            for lst, w in ((v_chunks, wv), (b_chunks, wb)):
                for rank, cid in enumerate(lst):
                    d = doc_of.get(cid)
                    if not d:
                        continue
                    s = w / (60 + rank + 1)
                    doc_sc[d] += s
                    best[d] = max(best[d], s)
            if prior:
                for d in doc_sc:
                    # 文档类型取该文档任一块的 doc_type
                    dt = next((doctype[c] for c in v_chunks + b_chunks if doc_of.get(c) == d), "")
                    w = prior.get(dt, 0.35)
                    doc_sc[d] *= (1.0 + strength * (w - 1.0))
                    best[d] *= (1.0 + strength * (w - 1.0))
            return sorted(doc_sc, key=lambda d: -doc_sc[d])[:TOP_K]

        ranked = {
            "R0_块级RRF等权": chunks_to_docs(r0),
            "R1_块级RRF加权0.5:1": chunks_to_docs(r1),
            "R2_意图软路由_块级": chunks_to_docs(r2),
            "R3_意图软路由_文档级": doc_level(1.0, 1.0, 0.6),
        }
        for cfg in cfgs:
            m = metrics_for_ranked(ranked[cfg], it["labels"])
            m["id"] = it["id"]
            m["layer"] = it["difficulty"]
            m["intent"] = intent_of(q)[0]
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
        "intent_dist": dict(ic),
        "note": "意图软路由 vs 全通道 RRF；不含重排序，用于判断重构方向",
        "results": {k: {"matrix": v["matrix"]} for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 意图路由对比（不含重排序）===")
    for cfg in cfgs:
        a = results[cfg]["matrix"]["ALL"]
        print(f"  {cfg:<24} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f} NDCG@10={a['ndcg10']:.4f}")
    print("\n=== 按意图拆解（R2 vs R0，Hit@1）===")
    for intent in sorted(ic):
        a0 = [x for x in rows_by_cfg["R0_块级RRF等权"] if x["intent"] == intent]
        a2 = [x for x in rows_by_cfg["R2_意图软路由_块级"] if x["intent"] == intent]
        if a0:
            h0 = sum(x["hit1"] for x in a0) / len(a0)
            h2 = sum(x["hit1"] for x in a2) / len(a2)
            print(f"  {intent:<8} n={len(a0):3d}  R0={h0:.3f}  R2={h2:.3f}  Δ{(h2-h0)*100:+.1f}pt")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
