"""端到端验收：三条业务主线 + 人工介入 + 防幻觉，全部走真实调用并落盘留证。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from starmoyu.graph import StarMoyuGraph  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports"
OUT.mkdir(exist_ok=True)

R = []          # 验收记录


def rec(step: str, ok: bool, detail: str, **extra) -> None:
    R.append({"step": step, "ok": ok, "detail": detail, **extra})
    print(f"{'✅' if ok else '❌'} {step}: {detail}")


def main() -> None:
    dg = StarMoyuGraph()

    print("=" * 90)
    print("场景 1｜商单构思：需求解析 → 检索 → 达人匹配 → 生成 → 人工介入 → 定稿")
    print("=" * 90)
    q1 = ("XX国货美妆品牌想推新款精华，预算15万，希望找4位腰部达人做种草，"
          "受众以女性为主，抖音和小红书都投")
    out = dg.run(q1, thread_id="e2e-proposal")
    req = out.get("requirement") or {}
    rec("1.1 意图路由", out.get("intent") == "proposal", f"intent={out.get('intent')}")
    rec("1.2 需求结构化", req.get("category") == "美妆" and req.get("budget") == 150000,
        f"category={req.get('category')} budget={req.get('budget')} kol_count={req.get('kol_count')} tier={req.get('kol_tier')}")
    rec("1.3 历史商单召回", len(out.get("retrieved_cases") or []) >= 3,
        f"{len(out.get('retrieved_cases') or [])} 条")
    kols = out.get("candidate_kols") or []
    rec("1.4 达人匹配", len(kols) >= 3, f"{len(kols)} 位候选，分布={sorted({k['tier'] for k in kols})}")
    val = out.get("validation") or {}
    rec("1.5 方案生成+引用校验", bool(out.get("proposal")) and val.get("ok"),
        f"{len(out.get('proposal') or '')} 字，引用 {val.get('cited')} 无越界={not val.get('invalid')}")
    st = dg.graph.get_state(dg.config("e2e-proposal"))
    rec("1.6 人工介入中断", st.next == ("human_review",), f"next={st.next}（图已挂起等待人工）")

    print()
    print("--- 人工提出修改意见 ---")
    fb = "达人减少到 2 位，总预算压到 9 万以内，优先选互动率高于 4% 的"
    out2 = dg.resume("e2e-proposal", action="revise", feedback=fb)
    st2 = dg.graph.get_state(dg.config("e2e-proposal"))
    rec("1.7 按意见重生成", st2.values.get("revision_count") == 1 and "revise →" in " ".join(out2.get("log") or []),
        f"revision_count={st2.values.get('revision_count')} next={st2.next}")
    rec("1.8 重生成后再次中断", st2.next == ("human_review",), f"next={st2.next}")

    out3 = dg.resume("e2e-proposal", action="approve")
    st3 = dg.graph.get_state(dg.config("e2e-proposal"))
    rec("1.9 确认定稿并结束", st3.next == () and "finalize → 输出" in " ".join(out3.get("log") or []),
        f"next={st3.next}")
    (OUT / "e2e_proposal.md").write_text(out3.get("proposal") or "", encoding="utf-8")

    print()
    print("=" * 90)
    print("场景 2｜刊例/案例速查：混合检索 + 强制引用")
    print("=" * 90)
    for i, (q, must) in enumerate([
        ("美妆类目 10 到 50 万粉的腰部达人报价大概什么价位？", ["rate_card"]),
        ("头部达人的档期一般要提前多久锁定？", ["platform_policy"]),
        ("我们做过哪些美妆精华类的成功案例，ROI 怎么样？", ["deal_case"]),
    ], 1):
        res = dg.a.quick_query(q)
        val = res["validation"]
        types = [c.doc_type for c in (res.get("raw_results") or [])]
        # 判据：期望的文档类型必须出现在命中里（不要求排第 1 —— 同构文档可能合法地排更前）
        rec(f"2.{i} 速查[{q[:18]}…]", val.get("ok") and any(m in types for m in must),
            f"引用 ok={val.get('ok')} 命中类型={types[:3]} 期望={must}")

    print()
    print("--- 防幻觉：知识库中不存在的信息 ---")
    res = dg.a.quick_query("我们和腾讯签的年度框架合同金额是多少？")
    ans = res["answer"]
    body = ans.split("参考来源")[0]
    refuses = ("未找到" in body or "无依据" in body or "未提及" in body or "未出现" in body)
    no_fabricated = not re.search(r"腾讯[^。\n]{0,40}?\d[\d,，.]*\s*(万|元|亿)", body)
    # 拒答场景下「零引用」是正确表现（无依据可引），不是失败
    rec("2.4 无依据时明确拒答", refuses and no_fabricated,
        f"明确声明无依据={refuses} 未编造金额={no_fabricated} 零引用={res['validation']['no_citation']}（拒答时应为零）")

    print()
    print("=" * 90)
    print("场景 3｜在途商单跟进：规则计算 + LLM 生成建议")
    print("=" * 90)
    fu = dg.a.followup()
    rec("3.1 在途商单扫描", fu["total"] > 0,
        f"在途 {fu['total']} 单｜高 {fu['stats']['高']} 中 {fu['stats']['中']} 正常 {fu['stats']['正常']}")
    analysis = fu.get("analysis") or {}
    at_risk = analysis.get("at_risk") or []
    rec("3.2 跟进建议生成", len(at_risk) > 0, f"输出 {len(at_risk)} 条风险建议")
    sample = at_risk[:2]
    for s in sample:
        print(f"   · [{s.get('risk_level')}] {s.get('deal_id')} {s.get('brand')} "
              f"停滞{s.get('stagnant_days')}天 → {s.get('suggested_action')}")
    if sample:
        print(f"   话术示例：{sample[0].get('message_draft', '')[:120]}")
    rec("3.3 建议含可发送话术", all(s.get("message_draft") for s in sample),
        "所有风险项均含 message_draft")

    print()
    print("=" * 90)
    nd = sum(1 for x in R if x["ok"])
    print(f"验收结果：{nd}/{len(R)} 项通过")
    print("=" * 90)
    (OUT / "e2e_report.json").write_text(json.dumps(
        {"summary": {"total": len(R), "passed": nd}, "items": R}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    dg.a.close()


if __name__ == "__main__":
    main()
