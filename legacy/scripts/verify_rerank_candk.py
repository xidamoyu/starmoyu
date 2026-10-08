"""验证 rerank_cand_k 优化的依据：GT 文档在「重排前」候选池 top-k 内的命中率。

背景：rerank_cand_k=10 的全部合理性建立在「GT 文档在重排前候选池 top-5 内命中率 100%」
这一测量上。此前该数字只有口头结论、没有落盘脚本与日志，属不可复现。

本脚本用与消融实验相同的评测集，逐条：
  1. 走「召回 → 元数据过滤 → 去重 → RRF → 类型先验 → 类型配额」，但**不做 rerank**
  2. 检查 GT 文档是否落在该候选池的 top-k 内
  3. 分别统计 k = 1/3/5/10/20/30，以及 rerank_cand_k=10 时是否覆盖 GT

输出 reports/rerank_candk_evidence.log 与 data/eval/rerank_candk_evidence.json。

用法：
  .venv/Scripts/python.exe scripts/verify_rerank_candk.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

OUT_LOG = ROOT / "reports" / "rerank_candk_evidence.log"
OUT_JSON = ROOT / "data" / "eval" / "rerank_candk_evidence.json"

from starmoyu.retriever import Retriever  # noqa: E402


def hit_rank(cands: list[str], labels: set[str], by_id: dict) -> int | None:
    """返回 GT 来源文件在候选池中的最高排名（1-based），未命中返回 None。

    判定口径与 evaluate.py 完全一致：用 chunk 的 source_file 匹配评测集 labels。
    """
    for i, cid in enumerate(cands, 1):
        ch = by_id.get(cid)
        if ch is not None and ch.source_file in labels:
            return i
    return None


def main() -> None:
    lines: list[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    # 复用 evaluate.py 的评测集构造，保证与消融实验同源
    from evaluate import build_eval_set  # type: ignore

    r = Retriever()
    items = build_eval_set(r)

    out("=" * 78)
    out("rerank_cand_k 优化依据验证：GT 文档在「重排前」候选池 top-k 命中率")
    out(f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    out("=" * 78)
    out(f"评测集：{len(items)} 条（与消融实验同源）")
    out()

    KS = (1, 3, 5, 10, 20, 30)
    ranks: list[int | None] = []
    per_item: list[dict] = []

    for it in items:
        q = it["query"]
        labels = set(it.get("labels") or [])
        # 关掉 rerank，但保留其余全部环节（与消融⑦组之前的配置一致）
        res = r.search(q, top_k=30, cand_k=30, rerank_cand_k=30, use_rerank=False)
        cands = [c.chunk_id for c in res["results"]]
        rk = hit_rank(cands, labels, r.by_id) if labels else None
        ranks.append(rk)
        per_item.append({"query": q, "type": it.get("type"), "gt_rank_in_pool": rk})

    n = len(items)
    out("--- GT 文档在候选池中的排名分布（未重排）---")
    for k in KS:
        c = sum(1 for x in ranks if x is not None and x <= k)
        out(f"  top-{k:<3d}: {c:3d}/{n}  = {c / n:.1%}")
    out()
    miss = sum(1 for x in ranks if x is None)
    out(f"  候选池内未命中(top-30 内): {miss}/{n}")
    out()

    # ---- 核心结论：rerank_cand_k=10 是否覆盖 GT ----
    covered10 = sum(1 for x in ranks if x is not None and x <= 10)
    covered30 = sum(1 for x in ranks if x is not None and x <= 30)
    out("--- rerank_cand_k 取值的覆盖性 ---")
    out(f"  rerank_cand_k=10 : 覆盖 GT {covered10}/{n} = {covered10 / n:.1%}")
    out(f"  rerank_cand_k=30 : 覆盖 GT {covered30}/{n} = {covered30 / n:.1%}")
    out()
    out("注：该脚本已关闭 rerank（use_rerank=False），因此统计的是「重排前」的候选池表现，")
    out("    这正是 rerank_cand_k 裁剪的安全边界依据。")

    data = {
        "n_items": n,
        "hit_at_k_before_rerank": {f"top_{k}": sum(1 for x in ranks if x is not None and x <= k) for k in KS},
        "covered_by_rerank_cand_k": {"10": covered10, "30": covered30},
        "missed_in_pool": miss,
        "per_item": per_item,
    }
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写入 {OUT_LOG.relative_to(ROOT)}")
    print(f"已写入 {OUT_JSON.relative_to(ROOT)}")
    r.close()


if __name__ == "__main__":
    main()
