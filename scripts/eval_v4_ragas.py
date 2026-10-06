# -*- coding: utf-8 -*-
"""生成质量评估脚本（v4）：同题号抽样复测 + 独立真判分通道 + 截断修复 + 忠实度归因。

v4 相对 v3 的变化：
  1. 判分通道：DashScope qwen-plus（真独立通道，与生成用的 token-plan qwen3.8 不同源）。
     v3 因欠费降级为 fallback:qwen3.8-max（与生成同源模型）。
  2. 判分上下文截断修复（v3 忠实度 0.812 归因之一）：改为分段判分——
     把回答按论断来源拆分为 ≤2 段，每段与完整上下文配对判分，取论断均值；
     上下文不再截 1000 字符（改 2800/段），论断数上限提到 10。
  3. 数值保真规则修正（v3 误报 83.3% 不合格）：
     - 只在回答的引用块范围内比对数值（正文 [n] 标注的语句 + 依据块 + 参考来源行）；
     - 排除标识符：商单编号（DC 前缀）、年份（19xx/20xx）、纯序号（1-99）；
     - 千分位归一化比对（4,561,264 ≈ 4561264）。
  4. 忠实度失分归因：对 supported=false 的论断，用判分通道二判归类——
     真实编造 / 上下文截断伪影 / 判分口径过严，统计各占比。
抽样与 v3 完全同题号（29 条），生成链路照旧（DealAssistant.quick_query）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402

storage._load_env()

EVAL_SET_PATH = ROOT / "reports" / "eval_set_v3.json"

# ---------------- 判分通道（真独立：DashScope qwen-plus） ----------------
JUDGE_KEY = os.environ.get("QWEN_API_KEY", "")
JUDGE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen-plus")


def judge_chat(system: str, user: str, max_tokens: int = 2000) -> str | None:
    if not JUDGE_KEY:
        return None
    import httpx
    try:
        r = httpx.post(JUDGE_URL, headers={"Authorization": f"Bearer {JUDGE_KEY}"},
                       json={"model": JUDGE_MODEL, "temperature": 0,
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}],
                             "max_tokens": max_tokens},
                       timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"  [判分通道 HTTP {r.status_code}: {r.text[:120]}]", flush=True)
        return None
    except Exception as e:
        print(f"  [判分通道异常 {type(e).__name__}: {str(e)[:100]}]", flush=True)
        return None


def judge_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    raw = (judge_chat(system, user, max_tokens) or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    s, e = raw.find("{"), raw.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(raw[s:e + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"判分 JSON 解析失败: {raw[:200]}")


# ---------------- RAGAS 指标 ----------------

FAITH_SYS = """你是严格的评估裁判。给定一段「回答片段」和它所依据的「完整上下文」，请从回答片段中抽取事实性论断（每条一个可独立判断真假的短句，去掉引用编号），并逐条判断该论断是否能从上下文中直接推出。
只输出 JSON：{"statements": [{"statement": "...", "supported": true/false}, ...]}
注意：寒暄与格式语不算论断；上下文中没有的信息即使正确也判 false；「上下文中未提及某信息」这类否定性陈述按其字面含义判断（若上下文确实未提及则为 true）。"""

ATTR_SYS = """你是评估仲裁员。一条回答论断被裁判判为「无法从上下文推出」。给定上下文与该论断，请判断失分原因属于哪类：
- "fabrication": 上下文明显支持相反内容，或论断无中生有（真实编造）；
- "truncation": 上下文里其实有支持该论断的内容，但裁判漏看/上下文过长漏判（截断伪影）；
- "strict": 论断是合理的归纳/汇总表述，超出了逐字推导范围但无事实错误（口径过严）。
只输出 JSON：{"category": "fabrication|truncation|strict", "reason": "一句话理由"}"""

REL_SYS = """你是评估裁判。给定一个「用户问题」和一段「回答」，请站在用户角度，从回答内容反推 1 个最能对应回答的原始问题。
只输出 JSON：{"reverse_question": "..."}"""


def faithfulness_segmented(answer: str, context: str) -> tuple[float | None, list]:
    """分段判分：回答拆为结论段与依据段，各自与完整上下文判分后按论断数加权。

    修复 v3 问题：上下文截 1000 字符 ×5 块导致支持内容被截掉、表格复述论断过多。
    """
    if not context.strip():
        return None, []
    # 拆段：结论/正文 与 依据；每段限长防判分输出超时
    body = re.split(r"###|## 依据", answer)[0]
    basis = ""
    m = re.search(r"依据[:：]?([\s\S]*?)(?=###|参考来源|$)", answer)
    if m:
        basis = m.group(1)
    segments = [s for s in (body[:1500], basis[:1500]) if s.strip()]
    all_sts: list[dict] = []
    per_seg_scores: list[float] = []
    for seg in segments:
        # 上下文按块给足长度：每块 2800 字符，最多 5 块（整块上下文 ~4000 字符，实际不会截）
        ctx_parts = context.split("\n\n")[:5]
        ctx_text = "\n\n".join(p[:2800] for p in ctx_parts)
        try:
            obj = judge_json(FAITH_SYS,
                             f"<上下文>\n{ctx_text}\n</上下文>\n\n<回答片段>\n{seg}\n</回答片段>")
        except Exception as e:
            print(f"  [忠实度分段判分失败: {e}]", flush=True)
            continue
        sts = obj.get("statements", [])[:10]
        if not sts:
            continue
        sup = sum(1 for s in sts if s.get("supported") is True)
        per_seg_scores.append(sup / len(sts))
        for s in sts:
            s["_segment"] = "body" if seg is segments[0] else "basis"
        all_sts.extend(sts)
    if not all_sts:
        return None, all_sts
    # 按论断数加权合并（等价于全论断均值）
    total = len(all_sts)
    supported = sum(1 for s in all_sts if s.get("supported") is True)
    return supported / total, all_sts


def attribute_failure(statement: str, context: str) -> str:
    ctx_text = "\n\n".join(p[:2800] for p in context.split("\n\n")[:5])
    try:
        obj = judge_json(ATTR_SYS,
                         f"<上下文>\n{ctx_text}\n</上下文>\n\n<论断>\n{statement}\n</论断>")
        cat = obj.get("category", "")
        if cat in ("fabrication", "truncation", "strict"):
            return cat
    except Exception as e:
        print(f"  [归因判分失败: {e}]", flush=True)
    return "unknown"


def answer_relevancy(query: str, answer: str) -> float | None:
    try:
        obj = judge_json(REL_SYS, f"<用户问题>\n{query}\n</用户问题>\n\n<回答>\n{answer[:1500]}\n</回答>")
    except Exception as e:
        print(f"  [相关性判分失败: {e}]", flush=True)
        return None
    rq = obj.get("reverse_question", "")
    if not rq:
        return None
    from starmoyu import llm
    v1 = llm.embed([query])[0]
    v2 = llm.embed([rq])[0]
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = sum(a * a for a in v1) ** 0.5
    n2 = sum(b * b for b in v2) ** 0.5
    return max(0.0, dot / (n1 * n2)) if n1 and n2 else 0.0


def context_precision_rank(ranked_meta: list[dict], labels: dict[str, str]) -> float | None:
    import math
    gains = []
    for c in ranked_meta:
        rel = labels.get(c["source_file"])
        gains.append(1.0 if rel == "direct" else (0.5 if rel == "assist" else 0.0))
    if all(g == 0 for g in gains):
        return 0.0
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted(gains, reverse=True)
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def context_recall(ranked_meta: list[dict], labels: dict[str, str]) -> float | None:
    total = sum(1.0 if r == "direct" else 0.5 for r in labels.values())
    if total == 0:
        return None
    hit = 0.0
    srcs = {c["source_file"] for c in ranked_meta}
    for sf, r in labels.items():
        if sf in srcs:
            hit += 1.0 if r == "direct" else 0.5
    return hit / total


# ---------------- 数值保真（v4 修正版规则） ----------------

NUM_RE = re.compile(r"\d[\d,，.]*\.?\d*")
# 标识符：商单编号（DC + 数字串，出现在 DC 字符串内）、年份、纯序号
DEAL_ID_RE = re.compile(r"DC[\dA-Za-z\-]*")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
SEQ_RE = re.compile(r"^\d{1,2}$")     # 引用序号/小序号（1-99）
DOC_NUM_RE = re.compile(r"^\d{1,3}$")  # 来源编号


def extract_numbers(text: str) -> list[str]:
    """抽取数值 token；先剥离商单编号（DC20260012 整体不算数值）。"""
    cleaned = DEAL_ID_RE.sub(" ", text)
    return [x.strip("，,。. ") for x in NUM_RE.findall(cleaned)]


def norm_num(x: str) -> str:
    return x.replace(",", "").replace("，", "")


def numeric_fidelity_v4(answer: str, context: str) -> tuple[bool, list, list]:
    """v4 规则：只在回答「引用块范围」内比对数值；排除标识符。

    引用块范围 = ① 含 [n] 引用标注的行/句 + 依据块 + 参考来源块（即所有声称有出处的部分）。
    返回 (是否全部通过, 违规数值明细, 排除的标识符明细)。
    """
    # 1) 剥离参考来源块（其中的编号行是元数据，且数值必来自上下文标题）
    src_start = answer.find("参考来源")
    body_part = answer[:src_start] if src_start != -1 else answer
    src_part = answer[src_start:] if src_start != -1 else ""

    # 2) 引用范围：含 [n] 的行 + 依据块行（"依据：" 之后所有行）——这些是声称有出处的数值载体
    lines = body_part.split("\n")
    cited_lines = []
    in_basis = False
    for ln in lines:
        if re.match(r"^\s*(依据|支撑)[:：]?", ln):
            in_basis = True
        if re.search(r"\[\d+\]", ln) or in_basis:
            cited_lines.append(ln)
        elif re.match(r"^\s*\d+\.\s", ln) or re.match(r"^\s*[-•]\s", ln):
            cited_lines.append(ln)  # 列表项通常带 [n]，整行纳入
    cited_text = "\n".join(cited_lines) + "\n" + src_part

    ctx_nums = {norm_num(x) for x in extract_numbers(context)}
    bad, excluded = [], []
    for x in extract_numbers(cited_text):
        n = norm_num(x)
        if YEAR_RE.match(n) or SEQ_RE.match(n) or DOC_NUM_RE.match(n):
            excluded.append(x)
            continue
        if n not in ctx_nums:
            bad.append(x)
    return (len(bad) == 0), bad, excluded


# ---------------- 主流程 ----------------

def main() -> None:
    t0 = time.time()
    es = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))
    items = es["items"]

    # 与 v3 完全同题号：复现 v3 的抽样
    def pick(layer: str, k: int) -> list[dict]:
        pool = [i for i in items if i["difficulty"] == layer]
        return pool[::max(1, len(pool) // k)][:k]

    sample = pick("L1", 8) + pick("L2", 8) + pick("L3", 8) + pick("L4", 5)
    print(f"抽样 {len(sample)} 条（与 v3 同题号）: " + ", ".join(i["id"] for i in sample),
          flush=True)

    # 探针：确认判分通道可用（真通道，不降级）
    probe = judge_chat("你是探针", "回复OK")
    if probe is None:
        print("!! 判分通道不可用，按约束如实记录并中止（不编造数字）", flush=True)
        sys.exit(2)
    print(f"判分通道（{JUDGE_MODEL}, DashScope 独立通道）可用", flush=True)

    from starmoyu import llm
    from starmoyu.assistant import DealAssistant
    from starmoyu.retriever import Retriever
    r = Retriever()
    asst = DealAssistant(retriever=r)

    results = []
    agg = {"faithfulness": [], "answer_relevancy": [], "context_precision": [],
           "context_recall": [], "numeric_ok": [], "over_reject": []}
    fail_categories = {"fabrication": 0, "truncation": 0, "strict": 0, "unknown": 0}
    fail_samples: list[dict] = []

    for it in sample:
        layer = it["difficulty"]
        labels = it["labels"]
        print(f"-- {it['id']} [{layer}] {it['query']}", flush=True)
        qa = asst.quick_query(it["query"], top_k=5)
        answer = qa["answer"]
        cites = qa["citations"]
        out = r.search(it["query"], top_k=5, cand_k=30)
        ctx, _ = r.build_context(out["results"], max_chars=4000)

        rec = {"id": it["id"], "difficulty": layer, "query": it["query"],
               "answer": answer, "cited_sources": [c["source_file"] for c in cites]}

        if layer == "L4":
            refuse_marks = ("未找到", "无依据", "未提及", "没有找到", "知识库中未")
            made_up = not any(m in answer for m in refuse_marks)
            rec["reject_correct"] = not made_up
            rec["faithfulness"] = None
            agg.setdefault("reject_correct", []).append(0.0 if made_up else 1.0)
            print(f"   拒答判定: {'编造(不合格)' if made_up else '正确拒答'}", flush=True)
        else:
            f_score, sts = faithfulness_segmented(answer, ctx)
            a_score = answer_relevancy(it["query"], answer)
            cp = context_precision_rank(cites, labels)
            cr = context_recall(cites, labels)
            ok, bad, excluded = numeric_fidelity_v4(answer, ctx)
            refuse_marks = ("未找到", "无依据", "未提及", "知识库中未")
            over_reject = any(m in answer for m in refuse_marks) and not any(
                s in set(labels) for s in [c["source_file"] for c in cites])
            rec.update({"faithfulness": f_score, "answer_relevancy": a_score,
                        "context_precision": cp, "context_recall": cr,
                        "numeric_ok": ok, "numeric_bad": bad,
                        "numeric_excluded_ids": excluded[:20], "over_reject": over_reject})
            # 忠实度失分归因
            if f_score is not None and f_score < 1.0:
                for s in sts:
                    if s.get("supported") is False:
                        cat = attribute_failure(s["statement"], ctx)
                        fail_categories[cat] += 1
                        fail_samples.append({"id": it["id"], "statement": s["statement"],
                                             "category": cat})
            if f_score is not None:
                agg["faithfulness"].append(f_score)
            if a_score is not None:
                agg["answer_relevancy"].append(a_score)
            if cp is not None:
                agg["context_precision"].append(cp)
            if cr is not None:
                agg["context_recall"].append(cr)
            agg["numeric_ok"].append(1.0 if ok else 0.0)
            if over_reject:
                agg["over_reject"].append(1.0)
            print(f"   忠实度={f_score and round(f_score,3)} 相关性={a_score and round(a_score,3)} "
                  f"上下文精确率={cp and round(cp,3)} 上下文召回率={cr and round(cr,3)} "
                  f"数值保真={'OK' if ok else 'FAIL:' + str(bad[:3])} "
                  f"(排除标识符 {len(excluded)} 个)", flush=True)
        results.append(rec)

    summary = {}
    for k, v in agg.items():
        if v:
            summary[k] = {"mean": sum(v) / len(v), "n": len(v)}
    l4 = [x for x in results if x["difficulty"] == "L4"]
    if l4:
        summary["reject_correct_rate"] = {
            "mean": sum(1 for x in l4 if x.get("reject_correct")) / len(l4), "n": len(l4)}
    total_fails = sum(fail_categories.values())
    attribution = {"total_failed_statements": total_fails}
    for k, v in fail_categories.items():
        attribution[k] = {"count": v,
                          "pct": round(v / total_fails * 100, 1) if total_fails else 0.0}

    out = {"run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
           "eval_set_version": es["eval_set_version"],
           "judge_model": JUDGE_MODEL,
           "judge_channel": "DashScope 独立通道（与生成 token-plan 不同源）",
           "sampling": "与 v3 同题号",
           "faithfulness_method": "分段判分（结论段+依据段，各与完整上下文配对，论断级合并）",
           "numeric_rule": "v4 修正版：仅引用块范围比对；排除商单编号(DC*)/年份/纯序号；千分位归一",
           "sample_ids": [x["id"] for x in sample],
           "summary": summary,
           "faithfulness_attribution": attribution,
           "faithfulness_fail_samples": fail_samples,
           "detail": results}
    (ROOT / "reports" / "ragas_eval_v4_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 汇总 ==\n{json.dumps(summary, ensure_ascii=False, indent=2)}", flush=True)
    print(f"== 失分归因 ==\n{json.dumps(attribution, ensure_ascii=False, indent=2)}", flush=True)
    print(f"总耗时 {time.time()-t0:.0f}s，落盘 ragas_eval_v4_results.json", flush=True)


if __name__ == "__main__":
    main()
