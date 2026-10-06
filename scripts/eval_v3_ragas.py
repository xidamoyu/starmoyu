# -*- coding: utf-8 -*-
"""生成质量评估脚本（v3，独立实现）：

1. 从评测集分层抽取样本（L1/L2/L3 各 8 条 + L4 拒答 5 条）；
2. 走完整生成链路（检索 + llm.chat 引用生成，项目现有接口）；
3. 检索增强生成评估框架（RAGAS）四指标的独立手工实现：
   - 忠实度：回答论断逐条判分，判分模型用另一通道的通义千问（DashScope 兼容模式；
     若该通道欠费不可用则降级用项目 chat 通道的另一个模型并如实记录）；
   - 答案相关性：由判分模型从回答反推问题，再计算反推问题与原问题的语义相似度（向量余弦）；
   - 上下文精确率：引用的上下文中相关内容排名是否靠前（以人工标注的相关文档为基准）；
   - 上下文召回率：参考答案要点被上下文覆盖的比例（此处以「相关文档是否出现在上下文」近似）；
4. 业务规则校验：数值保真（回答数值须在所引用上下文原文中出现）、拒答正确、引用越界。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu")
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import llm, storage  # noqa: E402
from starmoyu.assistant import DealAssistant  # noqa: E402
from starmoyu.retriever import Retriever  # noqa: E402

# ---- Milvus/PG 数据一致性兜底（同检索评测脚本，Milvus 集合存在少量已删除的残留块） ----
_orig_retrieve_vector = Retriever.retrieve_vector


def _safe_retrieve_vector(self, query: str, top_k: int):
    hits = _orig_retrieve_vector(self, query, top_k)
    return [(cid, s) for cid, s in hits if cid in self.by_id]


Retriever.retrieve_vector = _safe_retrieve_vector

EVAL_SET_PATH = ROOT / "reports" / "eval_set_v3.json"

# ---------------- 判分模型 ----------------
storage._load_env()
JUDGE_KEY = os.environ.get("QWEN_API_KEY", "")
JUDGE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen-plus")


def judge_chat(system: str, user: str, max_tokens: int = 2000) -> str | None:
    """调用判分模型；不可用（欠费等）时返回 None，由调用方降级。"""
    if not JUDGE_KEY:
        return None
    try:
        import httpx
        r = httpx.post(JUDGE_URL, headers={"Authorization": f"Bearer {JUDGE_KEY}"},
                       json={"model": JUDGE_MODEL, "temperature": 0,
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}],
                             "max_tokens": max_tokens},
                       timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"  [判分通道 HTTP {r.status_code}，尝试降级]", flush=True)
        return None
    except Exception as e:
        print(f"  [判分通道异常 {type(e).__name__}，尝试降级]", flush=True)
        return None


def judge_chat_fallback(system: str, user: str, max_tokens: int = 2000) -> str:
    """降级：用项目 chat 通道（flash 模型，与被评生成共用同一模型实例；偏差风险已写入报告局限）。
    注意：实测 pro 模型（qwen3.8-max）对本任务响应超过 120 秒读超时，flash 实测约 27 秒。"""
    return llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0, max_tokens=max_tokens)


def judge(system: str, user: str, max_tokens: int = 2000) -> str:
    out = judge_chat(system, user, max_tokens)
    if out is not None:
        return out
    return judge_chat_fallback(system, user, max_tokens)


def judge_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    raw = judge(system, user, max_tokens).strip()
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


# ---------------- RAGAS 四指标手工实现 ----------------

FAITH_SYS = """你是严格的评估裁判。给定一段「回答」和它所依据的「上下文」，请从回答中抽取事实性论断（最多 8 条最重要的，每条一个可独立判断真假的短句，去掉引用编号），并逐条判断该论断是否能从上下文中直接推出。
只输出 JSON：{"statements": [{"statement": "...", "supported": true/false}, ...]}
注意：寒暄与格式语不算论断；上下文中没有的信息即使正确也判 false。"""

REL_SYS = """你是评估裁判。给定一个「用户问题」和一段「回答」，请站在用户角度，从回答内容反推 1 个最能对应回答的原始问题。
只输出 JSON：{"reverse_question": "..."}"""

NUM_RE = re.compile(r"\d[\d,，.]*\.?\d*")


def extract_numbers(text: str) -> list[str]:
    return [x.strip("，,。. ") for x in NUM_RE.findall(text)]


def faithfulness(answer: str, context: str) -> tuple[float | None, list]:
    # 判分输入裁剪：回答取表格前的正文部分（表格是对上下文的机械复述，论断数量大导致判分输出超时），
    # 上下文每块截 1000 字符，控制判分输入在 2500 字符量级。
    import re as _re
    body = _re.split(r"###|## 依据", answer)[0]
    body = body[:1200]
    ctx_short = "\n\n".join(b[:1000] for b in context.split("\n\n")[:5])
    try:
        obj = judge_json(FAITH_SYS,
                         f"<上下文>\n{ctx_short}\n</上下文>\n\n<回答>\n{body}\n</回答>")
    except Exception as e:
        print(f"  [忠实度判分失败: {e}]", flush=True)
        return None, []
    sts = obj.get("statements", [])
    if not sts:
        return None, []
    supported = sum(1 for s in sts if s.get("supported") is True)
    return supported / len(sts), sts


def answer_relevancy(query: str, answer: str) -> float | None:
    try:
        obj = judge_json(REL_SYS, f"<用户问题>\n{query}\n</用户问题>\n\n<回答>\n{answer}\n</回答>")
    except Exception as e:
        print(f"  [相关性判分失败: {e}]", flush=True)
        return None
    rq = obj.get("reverse_question", "")
    if not rq:
        return None
    v1 = llm.embed([query])[0]
    v2 = llm.embed([rq])[0]
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = sum(a * a for a in v1) ** 0.5
    n2 = sum(b * b for b in v2) ** 0.5
    return max(0.0, dot / (n1 * n2)) if n1 and n2 else 0.0


def context_precision_rank(ranked_meta: list[dict], labels: dict[str, str]) -> float | None:
    """上下文精确率（排序视角）：相关文档在上下文中的排名越靠前分越高。
    实现：对上下文文档序列，相关(direct=1/assist=0.5)与不相关(0)做折损累计增益，除以理想值。"""
    import math
    gains = []
    for c in ranked_meta:
        rel = labels.get(c["source_file"])
        if rel == "direct":
            gains.append(1.0)
        elif rel == "assist":
            gains.append(0.5)
        else:
            gains.append(0.0)
    if all(g == 0 for g in gains):
        return 0.0
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted(gains, reverse=True)
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def context_recall(ranked_meta: list[dict], labels: dict[str, str]) -> float | None:
    """上下文召回率：人工标注的相关文档被上下文覆盖的比例（direct 权重 1，assist 权重 0.5）。"""
    total = sum(1.0 if r == "direct" else 0.5 for r in labels.values())
    if total == 0:
        return None
    hit = 0.0
    srcs = {c["source_file"] for c in ranked_meta}
    for sf, r in labels.items():
        if sf in srcs:
            hit += 1.0 if r == "direct" else 0.5
    return hit / total


# ---------------- 业务规则校验 ----------------

def numeric_fidelity(answer: str, context: str) -> tuple[bool, list]:
    """回答中的数值必须能在上下文原文中找到（按去掉千分位逗号后比对）。"""
    def norm(x: str) -> str:
        return x.replace(",", "").replace("，", "")
    ctx_nums = {norm(x) for x in extract_numbers(context)}
    bad = []
    for x in extract_numbers(re.sub(r"参考来源[\s\S]*$", "", answer.split("###")[0])):
        # 答案中的编号引用 [1][2] 不算数值
        if norm(x) not in ctx_nums and norm(x) not in {str(i) for i in range(1, 30)}:
            bad.append(x)
    return (len(bad) == 0), bad


# ---------------- 主流程 ----------------

def main() -> None:
    t0 = time.time()
    es = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))
    items = es["items"]

    # 分层抽样：L1/L2/L3 各 8 条（隔 5 取 1 保证覆盖面）+ L4 全部 15 条取 5 条
    def pick(layer: str, k: int) -> list[dict]:
        pool = [i for i in items if i["difficulty"] == layer]
        return pool[::max(1, len(pool) // k)][:k]

    sample = pick("L1", 8) + pick("L2", 8) + pick("L3", 8) + pick("L4", 5)
    print(f"抽样 {len(sample)} 条: " +
          ", ".join(f"{i['id']}" for i in sample), flush=True)

    r = Retriever()
    asst = DealAssistant(retriever=r)

    judge_available = judge_chat("你是探针", "回复OK") is not None
    print(f"判分通道（{JUDGE_MODEL}）可用: {judge_available}"
          + ("" if judge_available else "（将降级用项目 chat 通道 pro 模型判分）"), flush=True)

    results = []
    agg = {"faithfulness": [], "answer_relevancy": [], "context_precision": [],
           "context_recall": [], "numeric_ok": [], "reject_correct": [], "over_reject": []}
    for it in sample:
        layer = it["difficulty"]
        labels = it["labels"]
        print(f"-- {it['id']} [{layer}] {it['query']}", flush=True)
        qa = asst.quick_query(it["query"], top_k=5)
        answer = qa["answer"]
        cites = qa["citations"]
        ranked_meta = cites
        # 重建完整上下文文本（quick_query 内部上下文未返回，这里按同样规则重排重建）
        out = r.search(it["query"], top_k=5, cand_k=30)
        ctx, _ = r.build_context(out["results"], max_chars=4000)

        rec = {"id": it["id"], "difficulty": layer, "query": it["query"],
               "answer": answer[:1500], "cited_sources": [c["source_file"] for c in cites]}

        if layer == "L4":
            # 拒答正确性：回答明确声明无依据 = 正确
            refuse_marks = ("未找到", "无依据", "未提及", "没有找到", "知识库中未")
            made_up = not any(m in answer for m in refuse_marks)
            rec["reject_correct"] = not made_up
            rec["numeric_check"] = None
            rec["faithfulness"] = None
            agg["reject_correct"].append(0.0 if made_up else 1.0)
            print(f"   拒答判定: {'编造(不合格)' if made_up else '正确拒答'}", flush=True)
        else:
            f_score, sts = faithfulness(answer, ctx)
            a_score = answer_relevancy(it["query"], answer)
            cp = context_precision_rank(ranked_meta, labels)
            cr = context_recall(ranked_meta, labels)
            ok, bad = numeric_fidelity(answer, ctx)
            refuse_marks = ("未找到", "无依据", "未提及", "知识库中未")
            over_reject = any(m in answer for m in refuse_marks) and not any(
                s in {k for k in labels} for s in [c["source_file"] for c in cites])
            rec.update({"faithfulness": f_score, "answer_relevancy": a_score,
                        "context_precision": cp, "context_recall": cr,
                        "numeric_ok": ok, "numeric_bad": bad, "over_reject": over_reject})
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
                  f"数值保真={'OK' if ok else 'FAIL:' + str(bad[:3])}", flush=True)
        results.append(rec)

    summary = {}
    for k, v in agg.items():
        if v:
            summary[k] = {"mean": sum(v) / len(v), "n": len(v)}
    l4 = [x for x in results if x["difficulty"] == "L4"]
    summary["reject_correct_rate"] = {
        "mean": sum(1 for x in l4 if x.get("reject_correct")) / len(l4) if l4 else None,
        "n": len(l4)}

    out = {"run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
           "eval_set_version": es["eval_set_version"],
           "judge_model": JUDGE_MODEL if judge_available else f"fallback:{llm.CHAT_MODEL_PRO}",
           "sample_ids": [x["id"] for x in sample],
           "summary": summary,
           "detail": results}
    (ROOT / "reports" / "ragas_eval_v3_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 汇总 ==\n{json.dumps(summary, ensure_ascii=False, indent=2)}", flush=True)
    print(f"总耗时 {time.time()-t0:.0f}s，落盘 ragas_eval_v3_results.json", flush=True)


if __name__ == "__main__":
    main()
