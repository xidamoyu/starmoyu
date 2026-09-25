"""跨进程断点续跑演示：证明人工介入的 checkpoint 是真持久化（非内存态）。

流程（三个独立进程，模拟真实运维场景）：
  进程 A  run        → 生成方案，图在 human_review 处 interrupt 挂起，进程退出
  进程 B  state      → 新进程读取挂起状态，证明 checkpoint 存活于磁盘
  进程 C  resume     → 新进程带修改意见恢复执行，方案被改写后再次挂起/完成

若 Checkpointer 是 MemorySaver，进程 B 会读不到任何状态（本脚本会明确指出）。

用法：
  .venv/Scripts/python.exe scripts/demo_cross_process_resume.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DB = ROOT / "data" / "checkpoints.db"
LOG = ROOT / "reports" / "cross_process_resume.log"

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
TID = "crossproc-demo-001"


def run_child(step: str) -> tuple[int, str]:
    """在独立子进程中执行一个步骤，返回 (exit_code, 输出)。"""
    code = STEP_CODE[step]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["LANGGRAPH_CHECKPOINT_SQLITE"] = str(DB)   # 关键：切到 SQLite 持久化
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                       env=env, encoding="utf-8", errors="replace", timeout=600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


STEP_CODE = {
    "run": textwrap.dedent(f"""
        import sys; sys.path.insert(0, r"{SRC}")
        from starmoyu.graph import StarMoyuGraph
        # 覆盖 thread_id，保证三步操作同一条会话
        import starmoyu.graph as G
        dg = StarMoyuGraph()
        print("CHECKPOINTER:", dg.cp_kind, "| persistent:", dg.persistent)
        out = dg.run("帮我做一个美妆精华的新品种草商单方案，预算15万，4位腰部达人",
                     thread_id="{TID}")
        nxt = out.get("__interrupt__")
        print("INTERRUPT:", bool(nxt))
        print("NEXT:", dg.graph.get_state(dg.config("{TID}")).next)
        print("PROPOSAL_LEN:", len(out.get("proposal") or ""))
        print("REVISION:", out.get("revision_count"))
    """),
    "state": textwrap.dedent(f"""
        import sys; sys.path.insert(0, r"{SRC}")
        from starmoyu.graph import StarMoyuGraph
        dg = StarMoyuGraph()
        print("CHECKPOINTER:", dg.cp_kind, "| persistent:", dg.persistent)
        st = dg.graph.get_state(dg.config("{TID}"))
        print("FOUND_STATE:", bool(st and st.values))
        print("NEXT:", st.next)
        print("PROPOSAL_LEN:", len((st.values or {{}}).get("proposal") or ""))
    """),
    "resume": textwrap.dedent(f"""
        import sys; sys.path.insert(0, r"{SRC}")
        from starmoyu.graph import StarMoyuGraph
        dg = StarMoyuGraph()
        print("CHECKPOINTER:", dg.cp_kind, "| persistent:", dg.persistent)
        st = dg.graph.get_state(dg.config("{TID}"))
        print("RESUMED_FROM_NEXT:", st.next)
        out = dg.resume("{TID}", action="revise",
                        feedback="预算压缩到9万以内，达人减到2位，优先互动率高于4%的腰部达人")
        print("AFTER_REVISE_REVISION:", out.get("revision_count"))
        print("NEW_PROPOSAL_LEN:", len(out.get("proposal") or ""))
        st2 = dg.graph.get_state(dg.config("{TID}"))
        print("NEXT_AFTER:", st2.next)
    """),
}


def main() -> None:
    lines: list[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    if DB.exists():
        DB.unlink()

    out("=" * 78)
    out("跨进程断点续跑演示（人工介入的 checkpoint 是否真持久化）")
    out(f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    out(f"Checkpoint DB：{DB}")
    out("=" * 78)

    ok_run = ok_state = ok_resume = False

    # ---- 进程 A ----
    out("\n【进程 A】生成方案 → 应在 human_review 挂起后退出")
    rc, so = run_child("run")
    out(f"  exit={rc}")
    for ln in so.strip().splitlines():
        if ln.startswith(("CHECKPOINTER", "INTERRUPT", "NEXT", "PROPOSAL_LEN", "REVISION")):
            out(f"  {ln}")
    ok_run = (rc == 0) and ("INTERRUPT: True" in so) and ("human_review" in so)
    out(f"  → 进程 A 退出：{'成功挂起' if ok_run else '失败'}")

    out(f"\n  磁盘上的 checkpoint DB: {'存在' if DB.exists() else '不存在'}"
        f"({DB.stat().st_size if DB.exists() else 0} bytes)")

    # ---- 进程 B ----
    out("\n【进程 B】另一个新进程读取挂起状态（MemorySaver 在此会读不到）")
    rc, so = run_child("state")
    out(f"  exit={rc}")
    for ln in so.strip().splitlines():
        if ln.startswith(("CHECKPOINTER", "FOUND_STATE", "NEXT", "PROPOSAL_LEN")):
            out(f"  {ln}")
    ok_state = (rc == 0) and ("FOUND_STATE: True" in so)
    out(f"  → 跨进程读取状态：{'成功' if ok_state else '失败（state 丢失）'}")

    # ---- 进程 C ----
    out("\n【进程 C】第三个新进程带修改意见恢复执行")
    rc, so = run_child("resume")
    out(f"  exit={rc}")
    for ln in so.strip().splitlines():
        if ln.startswith(("CHECKPOINTER", "RESUMED_FROM", "AFTER_REVISE", "NEW_PROPOSAL", "NEXT_AFTER")):
            out(f"  {ln}")
    ok_resume = (rc == 0) and ("resumed" in so or "RESUMED_FROM_NEXT" in so)
    out(f"  → 跨进程恢复执行：{'成功' if ok_resume else '失败'}")

    out("\n" + "=" * 78)
    out(f"结论：A挂起={ok_run} / B跨进程读状态={ok_state} / C跨进程恢复={ok_resume}")
    if ok_run and ok_state and ok_resume:
        out("✅ Checkpointer 为持久化实现，人工介入可真正跨进程续跑")
    else:
        out("❌ 未通过：checkpoint 未能跨进程存活（可能仍为 MemorySaver）")
    out("=" * 78)

    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写入 {LOG.relative_to(ROOT)}")
    sys.exit(0 if (ok_run and ok_state and ok_resume) else 1)


if __name__ == "__main__":
    main()
