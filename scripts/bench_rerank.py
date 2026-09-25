"""Rerank 延迟基准：同口径对比「本地 CrossEncoder」与「云端 DashScope」。

背景：此前文档里的「32 ms/条」系用 1030ms 除以 32 条得出，而实际候选是 30 条，
属口径错误（应为 34.3 ms/条）。本脚本取代手工估算，输出可复现的实测值到 reports/。

口径说明：
  - 调用耗时 = 一次 rerank 请求（30 条候选）的墙钟时间
  - 单条均摊 = 调用耗时 / 候选条数（吞吐口径，非单条请求延迟）
  - 两者都报，避免再次出现口径混淆

本地栈需 torch + sentence-transformers（当前已卸载），故本地数值引用历史日志：
  reports/gpu_rerank_bench.log  (cpu/fp32 bs=8  30条 33.800s  1126.7ms/条)

用法：
  .venv/Scripts/python.exe scripts/bench_rerank.py
"""
from __future__ import annotations

import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import llm  # noqa: E402

OUT = ROOT / "reports" / "bench_rerank.log"
N_CAND = 30          # 与消融实验⑦组默认候选池一致
N_WARMUP = 2
N_RUN = 8

QUERY = "美妆类目10到50万粉的达人报价多少？"

DOCS = [
    "美妆类目达人刊例：粉丝10-50万，报价区间8000-25000元，CPM 25-60元。",
    "商单立项需先确认广告主预算与投放目标，再做达人组合方案。",
    "在途商单停滞超过7天判定为高风险，需商务介入跟进。",
    "3C数码类目达人以评测内容为主，互动率普遍低于美妆类目。",
    "平台规则：星图下单后需在48小时内完成达人确认，否则订单自动取消。",
] * 6  # 30 条


def main() -> None:
    lines: list[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    out("=" * 78)
    out("Rerank 延迟基准（云端 DashScope gte-rerank-v2）")
    out(f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    out("=" * 78)
    out(f"候选数：{N_CAND} 条／查询：「{QUERY}」")
    out(f"预热 {N_WARMUP} 次，测量 {N_RUN} 次")
    out()

    # 预热（消除连接建立与首次调用的影响）
    for _ in range(N_WARMUP):
        llm.rerank(QUERY, DOCS[:N_CAND], top_n=5)

    calls: list[float] = []
    for _ in range(N_RUN):
        t0 = time.perf_counter()
        res = llm.rerank(QUERY, DOCS[:N_CAND], top_n=5)
        calls.append((time.perf_counter() - t0) * 1000)

    calls_sorted = sorted(calls)
    med = statistics.median(calls)
    p95 = calls_sorted[int(len(calls_sorted) * 0.95) - 1]

    out("--- 逐次结果 ---")
    for i, c in enumerate(calls, 1):
        out(f"  第 {i} 次: {c:7.1f} ms")
    out()
    out("--- 汇总 ---")
    out(f"  调用耗时 中位数 : {med:8.1f} ms  (min {calls_sorted[0]:.1f} / max {calls_sorted[-1]:.1f} / p95 {p95:.1f})")
    out(f"  单条均摊(吞吐)  : {med / N_CAND:8.2f} ms/条   [= 中位调用耗时 / {N_CAND} 条]")
    out()

    # ---- 与本地栈对照（本地数值引自历史日志，因 torch 已卸载）----
    LOCAL_PER_CALL_MS = 33_800.0   # reports/gpu_rerank_bench.log: cpu/fp32 bs=8 30条 33.800s
    LOCAL_PER_ITEM_MS = 1126.7     # reports/gpu_rerank_bench.log: 1126.7 ms/条
    GPU_FP16_PER_ITEM_MS = 3225.0  # reports/gpu_fp16_bench.log: free 2.35G -> 0.00G

    speedup_call = LOCAL_PER_CALL_MS / med
    speedup_item = LOCAL_PER_ITEM_MS / (med / N_CAND)

    out("--- 同口径对照（重排同 %d 条候选）---" % N_CAND)
    out(f"  本地 CPU CrossEncoder : {LOCAL_PER_CALL_MS:8.0f} ms/调用  ({LOCAL_PER_ITEM_MS:.1f} ms/条)")
    out(f"  本地 GPU fp16         : {'更差':>8}           ({GPU_FP16_PER_ITEM_MS:.1f} ms/条，显存耗尽)")
    out(f"  云端 DashScope        : {med:8.0f} ms/调用  ({med / N_CAND:.1f} ms/条)")
    out(f"  → 提速（调用口径）    : {speedup_call:.1f}×")
    out(f"  → 提速（单条口径）    : {speedup_item:.1f}×")
    out()
    out("注：本地栈已卸载 torch，其数值引自 reports/gpu_rerank_bench.log 与 gpu_fp16_bench.log；")
    out("    如需重跑本地对照，先 `uv pip install torch sentence-transformers`。")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写入 {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
