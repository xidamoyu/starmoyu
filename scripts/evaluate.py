"""W2 评测：构造带 ground-truth 标签的评测集，跑消融实验。

评测集构造思路（可复现，不靠人工拍脑袋）：
  从真实数据结构反向生成问题，因此每条问题的「正确答案所在文档」是已知的 —— 
  这就是 ground truth 标签，可客观计算 Recall@k / MRR / Hit@k。
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from starmoyu.retriever import Retriever  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval"
rng = random.Random(42)


def build_eval_set(r: Retriever) -> list[dict]:
    """从库中真实数据反向构造问题 + ground truth 文档标签。"""
    items: list[dict] = []
    con = r.pg

    def _query(sql, args=()):
        cur = con.execute(sql, args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    # --- A 类：平台规则/方法论（labels = source_file 的唯一正确答案 + 同文档其他块也算命中）
    rules = [
        ("头部达人的档期一般要提前多久锁定？", "policies/巨量星图达人合作常见问题.md"),
        ("星图下单后达人多久内必须确认接单？", "policies/抖音星图下单与结算规则.md"),
        ("商单立项的预算怎么分配，达人费用占比多少？", "policies/商单立项与执行SOP.md"),
        ("内容修改次数有没有限制？", "policies/抖音星图下单与结算规则.md"),
        ("达人怎么按粉丝量分级？", "policies/巨量星图达人合作常见问题.md"),
        ("美妆类目投放有哪些合规红线？", "playbook/美妆类目投放方法论.md"),
        ("达人筛选的五维评估模型是什么？", "playbook/达人筛选评估框架.md"),
        ("提案的标准结构包含哪些部分？", "playbook/提案与报价话术要点.md"),
        ("面对广告主压价应该怎么谈？", "playbook/提案与报价话术要点.md"),
        ("美妆投放推荐的达人组合策略是什么？", "playbook/美妆类目投放方法论.md"),
        ("哪些达人属于硬性排除项？", "playbook/达人筛选评估框架.md"),
        ("平台服务费怎么收取？", "policies/抖音星图下单与结算规则.md"),
    ]
    for q, src in rules:
        items.append({"query": q, "labels": [src], "type": "规则/方法论", "difficulty": "easy"})

    # --- B 类：刊例数值（labels = 对应类目的刊例表）
    for cat in ["美妆", "母婴", "服饰", "家居", "汽车"]:
        items.append({"query": f"{cat}类目达人的刊例报价标准是怎样的？",
                      "labels": [f"rate_cards/{cat}类目达人刊例表.md"],
                      "type": "刊例数值", "difficulty": "easy"})
        items.append({"query": f"我想看{cat}类目 10 到 50 万粉的腰部达人报价",
                      "labels": [f"rate_cards/{cat}类目达人刊例表.md"],
                      "type": "刊例数值+条件过滤", "difficulty": "hard"})

    # --- C 类：历史案例（labels = 具体商单案例文档，从真实结案单反查）
    settled = _query("SELECT deal_id, brand_name, category, sub_category, result_metrics, budget "
                "FROM deal WHERE stage='结案' AND result_metrics IS NOT NULL")
    by_cat: dict[str, list] = {}
    for row in settled:
        by_cat.setdefault(row["category"], []).append(row)
    for cat, rows in by_cat.items():
        for row in rng.sample(rows, k=min(2, len(rows))):
            src = f"deal_cases/{row['deal_id']}_案例.md"
            items.append({
                "query": f"{row['brand_name']}那个{row['sub_category']}的商单是什么预算，ROI 多少？",
                "labels": [src], "type": "案例详情", "difficulty": "hard"})
    # 类目案例聚合类问题（允许多个正确答案）
    for cat, rows in by_cat.items():
        if len(rows) >= 2:
            sample = rng.sample(rows, k=min(4, len(rows)))
            items.append({
                "query": f"我们做过哪些{cat}类目的成功案例，效果怎么样？",
                "labels": [f"deal_cases/{x['deal_id']}_案例.md" for x in sample],
                "type": "案例聚合", "difficulty": "hard"})

    # --- D 类：在途商单（labels = 在途跟踪单）
    inflight = _query("SELECT deal_id, brand_name FROM deal "
                 "WHERE stage IN ('需求沟通','提案','签约','执行')")
    for row in rng.sample(inflight, k=min(8, len(inflight))):
        items.append({
            "query": f"{row['brand_name']}当前在途商单进行到哪个阶段了？",
            "labels": [f"deal_cases/inflight/{row['deal_id']}_跟踪单.md"],
            "type": "在途跟进", "difficulty": "medium"})

    return items


def evaluate(r: Retriever, items: list[dict], ks=(1, 3, 5, 10), **kw) -> dict:
    """计算 Hit@k、Recall@k、MRR。"""
    agg = {f"hit@{k}": 0 for k in ks}
    agg.update({f"recall@{k}": 0.0 for k in ks})
    mrr = 0.0
    per_type: dict[str, dict] = {}
    n = 0
    max_k = max(ks)
    for it in items:
        out = r.search(it["query"], top_k=max_k, cand_k=30, **kw)
        got = [c.source_file for c in out["results"]]
        labels = set(it["labels"])
        n += 1
        rr = 0.0
        for rank, src in enumerate(got, 1):
            if src in labels:
                rr = 1.0 / rank
                break
        mrr += rr
        rec = {k: len(set(got[:k]) & labels) / len(labels) for k in ks}
        t = per_type.setdefault(it["type"], {"n": 0, "hit@5": 0, "mrr": 0.0})
        t["n"] += 1
        t["hit@5"] += 1 if set(got[:5]) & labels else 0
        t["mrr"] += rr
        for k in ks:
            if set(got[:k]) & labels:
                agg[f"hit@{k}"] += 1
            agg[f"recall@{k}"] += rec[k]
    res = {k: (v / n if "recall" in k else v / n) for k, v in agg.items()}
    res["mrr"] = mrr / n
    res["n"] = n
    for t in per_type.values():
        t["hit@5"] /= t["n"]
        t["mrr"] /= t["n"]
    res["per_type"] = per_type
    return res


EXPERIMENTS = [
    ("① 纯向量召回 (Baseline)", dict(use_bm25=False, use_vector=True, use_rrf=False, use_rerank=False, use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("② + BM25 多路召回 (RRF)", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False, use_meta_filter=False, use_prior=False, use_dedupe=False, use_quota=False)),
    ("③ + 文档级去重", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False, use_meta_filter=False, use_prior=False, use_dedupe=True, use_quota=False)),
    ("④ + 元数据预过滤", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False, use_meta_filter=True, use_prior=False, use_dedupe=True, use_quota=False)),
    ("⑤ + 文档类型先验", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False, use_meta_filter=True, use_prior=True, use_dedupe=True, use_quota=False)),
    ("⑥ + 类型配额保底", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=False, use_meta_filter=True, use_prior=True, use_dedupe=True, use_quota=True)),
    ("⑦ + Rerank 重排 (Full)", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=True, use_meta_filter=True, use_prior=True, use_dedupe=True, use_quota=True, rerank_cand_k=30)),
    ("⑧ + 收窄重排候选 (rerank_cand_k=10)", dict(use_bm25=True, use_vector=True, use_rrf=True, use_rerank=True, use_meta_filter=True, use_prior=True, use_dedupe=True, use_quota=True, rerank_cand_k=10)),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    r = Retriever()
    print(f"索引: chunk={len(r.chunks)}\n")

    items = build_eval_set(r)
    (OUT / "eval_set.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"评测集: {len(items)} 条")
    from collections import Counter
    print("  类型分布:", dict(Counter(i["type"] for i in items)))
    print("  难度分布:", dict(Counter(i["difficulty"] for i in items)))
    print()

    results = []
    for name, kw in EXPERIMENTS:
        t0 = time.perf_counter()
        res = evaluate(r, items, **kw)
        dt = time.perf_counter() - t0
        per_q_ms = dt / max(res["n"], 1) * 1000
        # 记录重排耗时占比：只有开了 rerank 的组才有意义
        res["wall_s"] = round(dt, 2)
        res["ms_per_query"] = round(per_q_ms, 1)
        results.append({"name": name, "wall_s": res["wall_s"], "ms_per_query": res["ms_per_query"],
                        **{k: v for k, v in res.items() if k not in ("per_type", "wall_s", "ms_per_query")}})
        print(f"{name:34s} Hit@1={res['hit@1']:.3f} Hit@5={res['hit@5']:.3f} "
              f"Hit@10={res['hit@10']:.3f} Recall@5={res['recall@5']:.3f} MRR={res['mrr']:.3f} "
              f"| {res['wall_s']:7.1f}s  {res['ms_per_query']:7.1f}ms/q")

    (OUT / "ablation_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 最后一次实验的分类型明细
    detail = evaluate(r, items, **EXPERIMENTS[-1][1])
    (OUT / "per_type_detail.json").write_text(
        json.dumps(detail["per_type"], ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n按问题类型（完整链路）:")
    for t, v in detail["per_type"].items():
        print(f"  {t:20s} n={v['n']:3d} Hit@5={v['hit@5']:.3f} MRR={v['mrr']:.3f}")
    r.close()


if __name__ == "__main__":
    main()
