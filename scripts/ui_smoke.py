"""前端逻辑冒烟测试：直接执行 app.py 三个 tab 依赖的真实调用链。

Streamlit 页面渲染依赖浏览器（当前环境无 Chromium），因此改为验证
「点击按钮后实际执行的代码路径」，这是页面能否真正工作的实质判据。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu.graph import StarMoyuGraph  # noqa: E402

R: list[tuple[str, bool, str]] = []


def rec(step: str, ok: bool, detail: str) -> None:
    R.append((step, ok, detail))
    print(f"{'OK ' if ok else 'FAIL'} {step}: {detail}")


def main() -> None:
    dg = StarMoyuGraph()

    # ---- Tab1 商单构思：含人工确认面板所依赖的 resume 能力
    print("=" * 80)
    print("Tab1 商单构思")
    print("=" * 80)
    q = ("XX国货美妆品牌想推新款精华，预算15万，希望找4位腰部达人做种草，受众以女性为主")
    out = dg.run(q, thread_id="ui-t1")
    rec("1.1 dg.run 返回", bool(out), f"intent={out.get('intent')}")
    rec("1.2 方案正文", bool(out.get("proposal")), f"{len(out.get('proposal') or '')} 字")
    st = dg.graph.get_state(dg.config("ui-t1"))
    rec("1.3 中断在 human_review（确认按钮可用）", st.next == ("human_review",), f"next={st.next}")
    rec("1.4 候选达人表数据", len(out.get("candidate_kols") or []) > 0,
        f"{len(out.get('candidate_kols') or [])} 位（供 st.dataframe 渲染）")
    rec("1.5 执行轨迹", len(out.get("log") or []) >= 4, f"{len(out.get('log') or [])} 条")
    # 模拟点击「提出修改」
    fb = "达人减少到 2 位，预算压到 9 万以内"
    out2 = dg.resume("ui-t1", action="revise", feedback=fb)
    rec("1.6 修改按钮生效", dg.graph.get_state(dg.config("ui-t1")).values.get("revision_count") == 1,
        f"revision_count={dg.graph.get_state(dg.config('ui-t1')).values.get('revision_count')}")
    # 模拟点击「确认通过」
    out3 = dg.resume("ui-t1", action="approve")
    rec("1.7 确认按钮结束流程", dg.graph.get_state(dg.config("ui-t1")).next == (), "next=()")

    # ---- Tab2 刊例速查
    print()
    print("=" * 80)
    print("Tab2 刊例/案例速查")
    print("=" * 80)
    for i, qq in enumerate([
        "商单立项的预算怎么分配，达人费用占比多少？",
        "美妆类目 10 到 50 万粉的腰部达人报价大概什么价位？",
    ], 1):
        r = dg.a.quick_query(qq)
        rec(f"2.{i} 速查[{qq[:16]}…]", bool(r.get("answer")),
            f"{len(r['answer'])} 字，引用={r['validation'].get('cited')}，"
            f"命中片段={len(r.get('raw_results') or [])}")

    # ---- Tab3 在途跟进
    print()
    print("=" * 80)
    print("Tab3 在途商单跟进")
    print("=" * 80)
    fu = dg.a.followup()
    rec("3.1 扫描在途商单", fu["total"] > 0,
        f"在途 {fu['total']}，高 {fu['stats']['高']} 中 {fu['stats']['中']} 正常 {fu['stats']['正常']}")
    ar = (fu.get("analysis") or {}).get("at_risk") or []
    rec("3.2 跟进建议", len(ar) > 0, f"{len(ar)} 条（供 st.json 渲染）")
    rec("3.3 原始数据表", len(fu.get("raw") or []) > 0, f"{len(fu.get('raw') or [])} 行")

    print()
    print("=" * 80)
    ok = sum(1 for _, o, _ in R if o)
    print(f"前端逻辑冒烟：{ok}/{len(R)} 通过")
    print("=" * 80)
    dg.a.close()


if __name__ == "__main__":
    main()
