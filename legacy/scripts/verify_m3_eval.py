"""M3 评测回归：54 条评测集跑完整链路（多路召回+Rerank），Hit@1/MRR/Recall@5 不得回退。

保护基线（PROGRESS.md 锁定）：
  Hit@1 0.778 / Hit@5 1.000 / Recall@5 0.986 / MRR 0.883 (cand_k=10)

运行（后台）：.venv/Scripts/python.exe scripts/verify_m3_eval.py
输出：reports/verify_m3_eval.log + data/eval/m3_regression.json（单一来源）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu.retriever import Retriever  # noqa: E402

BASELINE = {"hit1": 0.778, "hit5": 1.000, "recall5": 0.986, "mrr": 0.883}
TOL = 0.02  # 容忍 ±0.02 抖动（LLM/服务无回归时检索层应稳定）


def main() -> int:
    ev_path = ROOT / "data" / "eval" / "eval_set.json"
    items = json.loads(ev_path.read_text(encoding="utf-8"))
    print(f"=== M3 评测回归：{len(items)} 条，完整链路 cand_k=10 ===")
    r = Retriever()

    hit1 = hit5 = 0
    recall_sum = 0.0
    rr_sum = 0.0
    per_type: dict[str, dict] = {}
    t0 = time.time()
    for i, item in enumerate(items):
        labels = set(item["labels"])
        out = r.search(item["query"], top_k=5, cand_k=10)
        got = [c.source_file for c in out["results"]]
        rank = next((j + 1 for j, s in enumerate(got) if s in labels), None)
        if rank == 1:
            hit1 += 1
        if rank is not None and rank <= 5:
            hit5 += 1
        recall_sum += len(labels & set(got[:5])) / len(labels) if labels else 0
        rr_sum += 1.0 / rank if rank else 0.0
        t = item.get("type", "?")
        d = per_type.setdefault(t, {"n": 0, "hit1": 0, "rr": 0.0})
        d["n"] += 1
        d["hit1"] += 1 if rank == 1 else 0
        d["rr"] += 1.0 / rank if rank else 0.0
        if (i + 1) % 10 == 0:
            print(f"  ... {i+1}/{len(items)} ({time.time()-t0:.0f}s)")

    n = len(items)
    metrics = {
        "hit1": round(hit1 / n, 3), "hit5": round(hit5 / n, 3),
        "recall5": round(recall_sum / n, 3), "mrr": round(rr_sum / n, 3),
        "n": n, "cand_k": 10, "elapsed_s": round(time.time() - t0, 1),
        "per_type": {t: {"n": d["n"], "hit1": round(d["hit1"] / d["n"], 3),
                         "mrr": round(d["rr"] / d["n"], 3)}
                     for t, d in per_type.items()},
    }
    out = ROOT / "data" / "eval" / "m3_regression.json"
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in metrics.items() if k != "per_type"},
                     ensure_ascii=False, indent=2))
    regress = []
    for k in ("hit1", "hit5", "recall5", "mrr"):
        if metrics[k] < BASELINE[k] - TOL:
            regress.append(f"{k}: {metrics[k]} < 基线 {BASELINE[k]}-tol")
    if regress:
        print("❌ 回归：", "; ".join(regress))
        print(f"明细已存 {out}")
        return 1
    print("✅ 无回退（基线 Hit@1 0.778 / MRR 0.883 / Recall@5 0.986）")
    print(f"明细已存 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
