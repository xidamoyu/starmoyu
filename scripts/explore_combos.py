# -*- coding: utf-8 -*-
"""枚举可出题的组合（真值篇数 2~10，保证 Recall@10 不应被窗口截断）。
只打印统计，供人工选题。
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval_corpus_index import derive_deal_docs, inflight  # noqa: E402

docs = derive_deal_docs()
print(f"总商单文档 {len(docs)}")

by = defaultdict(list)
for d in docs:
    by[(d["kind"], d["stage"])].append(d)
print({k: len(v) for k, v in by.items()})

print("\n=== 结案案例：类目 × 篇数 ===")
c = Counter(d["category"] for d in docs if d["kind"] == "case")
for k, v in c.most_common():
    print(f"  {k}: {v}")

print("\n=== 结案案例：类目+目标 × 篇数 (2~10) ===")
c2 = Counter((d["category"], d["goal"]) for d in docs if d["kind"] == "case")
for (cat, goal), v in sorted(c2.items(), key=lambda x: -x[1]):
    if 2 <= v <= 10:
        print(f"  {cat} + {goal}: {v}")

print("\n=== 结案案例：类目+平台 × 篇数 (2~10) ===")
c3 = Counter((d["category"], d["platform"]) for d in docs if d["kind"] == "case")
for (cat, plat), v in sorted(c3.items(), key=lambda x: -x[1]):
    if 2 <= v <= 10:
        print(f"  {cat} + {plat}: {v}")

print("\n=== 在途：类目 × 篇数 ===")
ic = Counter(d["category"] for d in docs if inflight(d))
for k, v in ic.most_common(12):
    print(f"  {k}: {v}")

print("\n=== 在途：目标+平台 × 篇数 (2~10) ===")
c4 = Counter((d["goal"], d["platform"]) for d in docs if inflight(d))
for (g, p), v in sorted(c4.items(), key=lambda x: -x[1]):
    if 2 <= v <= 10:
        print(f"  {g} + {p}: {v}")

print("\n=== 在途：类目+平台 × 篇数 (2~10) ===")
c5 = Counter((d["category"], d["platform"]) for d in docs if inflight(d))
for (cat, p), v in sorted(c5.items(), key=lambda x: -x[1]):
    if 2 <= v <= 10:
        print(f"  {cat} + {p}: {v}")

print("\n=== 在途：阶段分布 ===")
print(Counter(d["stage"] for d in docs if inflight(d)))

print("\n=== 品牌 × 篇数 (2~10) ===")
bc = Counter(d["brand"] for d in docs)
for k, v in sorted(bc.items(), key=lambda x: -x[1]):
    if 2 <= v <= 10:
        print(f"  {k}: {v}")

print("\n=== 预算区间 × 篇数（结案，2~10）===")
for lo, hi in ((0, 5), (5, 10), (10, 20), (20, 50), (50, 1000)):
    n = sum(1 for d in docs if d["kind"] == "case" and d["budget_wan"] and lo <= d["budget_wan"] < hi)
    print(f"  {lo}-{hi} 万: {n}")

print("\n=== ROI 区间 × 篇数（结案有指标 75 篇中）===")
rois = [d["roi"] for d in docs if d["kind"] == "case" and d["roi"]]
print("  roi 样本", len(rois), "min/max", min(rois), max(rois))
for t in (1.0, 1.2, 1.5, 1.8, 2.0, 2.5):
    n = sum(1 for r in rois if r >= t)
    print(f"  ROI>={t}: {n}")
