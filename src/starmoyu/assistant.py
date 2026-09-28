"""生成层：RAG 问答 + 引用溯源 + 防幻觉校验。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starmoyu import llm  # noqa: E402
from starmoyu.retriever import Retriever  # noqa: E402

SYS_QA = """你是 MCN 商单资产的资深运营助理，服务对象是商单运营与商务同事。

## 铁律
1. 只依据 <context> 中的信息作答，不得使用任何外部知识或常识推断。
2. 每条事实性结论后必须紧跟引用编号，格式 [1]、[2] 或 [1][3]。
3. 若 <context> 中没有足够信息，必须明确回答「知识库中未找到相关依据」，并说明缺什么，禁止编造。
4. 涉及金额、比例、天数、粉丝量等数值时，必须原样引用，不得四舍五入改写或估算。
5. 不得引用 <context> 中不存在的编号。

## 输出格式
先给出结论，再给依据。结尾固定输出：
### 参考来源
[1] {文档标题} · {小节} · {来源文件}
（按实际引用编号列出）
"""

SYS_PROPOSAL = """你是资深 MCN 商单策划，负责基于公司私有历史商单资产，产出可直接给广告主看的商单构思方案。

## 铁律
1. 所有结论必须能追溯到 <context>，用 [1][2] 标注来源。
2. 报价只能来自 <context> 中的刊例或历史商单，禁止自行估算；若无依据，写「暂无刊例依据，需商务确认」。
3. 达人数据（粉丝量、互动率、报价）必须来自 <candidate_kols> 或 <context>，不得编造。
4. 效果预估必须写明依据（引用历史同类商单数据），不得凭感觉给数字。
5. 若检索到的历史商单与当前需求类目不同，需指出差异并说明可迁移的经验，不要硬套。

## 输出格式（Markdown）
## 一、需求理解
## 二、达人组合建议
| 达人ID | 昵称 | 量级/粉丝 | 匹配理由 | 刊例价 | 预估曝光 |
## 三、执行节奏
## 四、预算分配表
## 五、效果预估与依据
## 六、风险提示
## 七、参考来源
"""

SYS_ROUTER = """你是意图分类器。判断用户问题属于哪一类，只输出 JSON。

类别定义：
- proposal：需要生成/设计商单构思方案（如"帮我做个方案""怎么策划这次投放"）
- query：查询类，问规则/报价/案例/数据（如"报价多少""有什么规则""做过哪些案例"）
- followup：查询在途商单状态与跟进建议（如"哪些商单卡住了""跟进情况"）
- chat：寒暄或与业务无关

输出：{"intent": "上述四类之一", "reason": "一句话理由"}"""

SYS_FOLLOWUP = """你是商单跟进助理。基于在途商单数据识别风险并生成跟进建议。

## 判断规则（停滞天数已由脚本计算，你负责解释与措辞）
- 停滞 ≥ 7 天，或计划结案日期已过仍未结案 → 高风险
- 停滞 ≥ 3 天 → 中风险
- 其他 → 正常

## 要求
1. 建议话术要专业、简洁，可直接复制发送给同事。
2. 只基于输入数据，不得编造商单信息。
3. 只输出 JSON。

## 输出结构
{"summary": "整体情况一句话", "at_risk": [{"deal_id": "...", "brand": "...", "stage": "...",
 "stagnant_days": 数字, "risk_level": "高/中/正常", "reason": "...", "suggested_action": "...",
 "message_draft": "可直接发送的话术"}]}"""


# ---------------------------------------------------------------- 输出校验

def render_sources(cites: list[dict], used: list[int] | None = None) -> str:
    """由代码确定性渲染「参考来源」区块，不依赖模型自觉输出。"""
    if not cites:
        return ""
    sel = [c for c in cites if used is None or c["n"] in used]
    if not sel:
        sel = cites
    lines = ["", "### 参考来源"]
    for c in sel:
        loc = f" · {c['section']}" if c.get("section") else ""
        lines.append(f"[{c['n']}] {c['doc_title']}{loc} · {c['source_file']}")
    return "\n".join(lines)


def attach_sources(answer: str, cites: list[dict], n_context: int) -> tuple[str, dict]:
    """补齐参考来源区块，并返回引用校验结果。"""
    cited = sorted({int(x) for x in re.findall(r"\[(\d+)\]", answer)})
    cited = [n for n in cited if 1 <= n <= n_context]
    if "参考来源" not in answer:
        answer = answer.rstrip() + "\n" + render_sources(cites, cited or None)
    val = validate_citations(answer, n_context)
    val["cited_body"] = cited          # 正文中实际引用的编号
    return answer, val


def validate_citations(answer: str, n_context: int) -> dict:
    """校验引用编号是否越界，抽取引用列表。"""
    body = answer.split("参考来源")[0]
    nums = {int(x) for x in re.findall(r"\[(\d+)\]", body)}
    invalid = sorted(n for n in nums if n < 1 or n > n_context)
    has_section = "参考来源" in answer
    return {
        "cited": sorted(nums),
        "invalid": invalid,
        "has_source_section": has_section,
        "no_citation": len(nums) == 0,
        "ok": not invalid and has_section and bool(nums),
    }


def enforce_unknown_on_empty(answer: str) -> str:
    """答案缺少引用且未声明无依据时，附加提示（保守防幻觉）。"""
    if not re.search(r"\[\d+\]", answer) and "未找到" not in answer and "无依据" not in answer:
        return answer + "\n\n> ⚠️ 本次回答未引用知识库来源，请人工复核。"
    return answer


# ---------------------------------------------------------------- 对外接口

class DealAssistant:
    def __init__(self, retriever: Retriever | None = None):
        self.r = retriever or Retriever()

    def route(self, query: str) -> dict:
        try:
            return llm.chat_json([{"role": "system", "content": SYS_ROUTER},
                                  {"role": "user", "content": query}], max_tokens=128)
        except Exception:
            return {"intent": "query", "reason": "路由失败，降级为查询"}

    def quick_query(self, query: str, top_k: int = 5) -> dict:
        out = self.r.search(query, top_k=top_k)
        ctx, cites = self.r.build_context(out["results"], max_chars=4000)
        if not ctx.strip():
            return {"answer": "知识库中未找到相关依据。", "citations": [], "trace": out["info"]}
        ans = self._answer_with_citation_retry(query, ctx, cites)
        ans = enforce_unknown_on_empty(ans)
        ans, val = attach_sources(ans, cites, len(cites))
        return {"answer": ans, "citations": cites, "trace": out["info"],
                "validation": val, "raw_results": out["results"]}

    @staticmethod
    def _answer_with_citation_retry(query: str, ctx: str, cites: list[dict],
                                    attempts: int = 3) -> str:
        """生成答案并要求带引用；模型偶发漏引用时重试。

        LLM 输出不稳定：即使 System Prompt 强制要求，仍会偶发整段不带 [n] 标注。
        此类回答无法溯源，等于把幻觉风险直接暴露给用户，因此重试而非放行。
        """
        last = ""
        for i in range(attempts):
            msgs = [
                {"role": "system", "content": SYS_QA},
                {"role": "user", "content": f"<context>\n{ctx}\n</context>\n\n【问题】{query}"},
            ]
            if i > 0:
                # 二次起追加显式纠正指令
                msgs.append({"role": "assistant", "content": last[:400]})
                msgs.append({"role": "user", "content":
                             "你的上一条回答没有任何 [n] 引用标注。请重新回答，"
                             "并确保每条事实性结论后都紧跟来源编号 [1][2] 等。"})
            last = llm.chat(msgs, temperature=0.1, max_tokens=1500)
            # 有引用、或明确声明无依据，即视为合格
            import re as _re
            if _re.search(r"\[\d+\]", last) or any(
                    k in last for k in ("未找到", "无依据", "未提及", "未出现")):
                return last
        return last   # 重试用尽则返回最后一次（由 enforce_unknown_on_empty 打警告）

    # ---- 工具：达人检索（SQL 硬筛选 + 语义排序） ----
    def tool_kol_search(self, category: str, fans_min: int | None = None, fans_max: int | None = None,
                        budget_per_kol: float | None = None, tier: str | None = None,
                        semantic_query: str = "", top_k: int = 8,
                        budget_scope: str = "avg") -> list[dict]:
        """达人检索。

        budget_scope:
          avg  —— 预算上限按「均摊价」理解，用 ±60% 宽松区间过滤（默认，符合真实业务：
                  头部达人贵、尾部便宜，组合投放不会人人都低于均价）
          hard —— 严格过滤，用于明确的「单价不超过 X」诉求
        """
        sql = "SELECT * FROM kol_profile WHERE 1=1"
        args: list = []
        if category:
            sql += " AND category=%s"
            args.append(category)
        if tier:
            sql += " AND tier=%s"
            args.append(tier)
        if fans_min is not None:
            sql += " AND fans_count>=%s"
            args.append(fans_min)
        if fans_max is not None:
            sql += " AND fans_count<=%s"
            args.append(fans_max)
        if budget_per_kol is not None:
            cap = float(budget_per_kol) if budget_scope == "hard" else float(budget_per_kol) * 1.6
            sql += " AND price_21_60s<=%s"
            args.append(cap)
        cur = self.r.pg.execute(sql, args)
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        if not rows and (tier or budget_per_kol is not None):
            # 条件过窄：逐级放宽（去掉预算 → 去掉量级），保证方案不至于只有 1 位候选
            return self.tool_kol_search(category=category, fans_min=fans_min, fans_max=fans_max,
                                        tier=None, budget_per_kol=None,
                                        semantic_query=semantic_query, top_k=top_k)
        if not rows:
            return []
        if semantic_query and len(rows) > 1:
            # 语义排序：把达人档案拼成文本，与查询算相似度
            docs = [f"{x['kol_name']} {x['category']} {x['sub_category']} "
                    f"{' '.join(_as_list(x.get('tags')))} {x['region']} "
                    f"互动率{x['interact_rate']} 转化率{x['conversion_rate']}" for x in rows]
            try:
                vecs = llm.embed([semantic_query] + docs)
                import numpy as np
                q = np.asarray(vecs[0], dtype=np.float32)
                M = np.asarray(vecs[1:], dtype=np.float32)
                q /= max(np.linalg.norm(q), 1e-9)
                M /= np.clip(np.linalg.norm(M, axis=1, keepdims=True), 1e-9, None)
                sims = M @ q
                for i, x in enumerate(rows):
                    x["_sem"] = float(sims[i])
                rows.sort(key=lambda x: -x["_sem"])
            except Exception:
                pass
        for x in rows[:top_k]:
            cpm = x.get('cpm')
            cpm_s = f"；CPM ¥{cpm:,.0f}" if isinstance(cpm, (int, float)) else ""
            av = x.get('avg_views')
            av_s = f"；均播 {av/10000:.1f}w" if isinstance(av, (int, float)) else ""
            x["match_reason"] = (
                f"{x['category']}/{x['sub_category']} 类目匹配；{x['tier']}达（{x['fans_count']/10000:.1f}w 粉）；"
                f"标签 {'、'.join(_as_list(x.get('tags')))}；"
                f"互动率 {x['interact_rate']*100:.2f}%；合作评级 {x['cooperation_level']}；"
                f"刊例 ¥{x['price_21_60s']:,}{cpm_s}{av_s}")
        return rows[:top_k]

    def proposal(self, requirement: str, top_k: int = 5) -> dict:
        """商单构思生成：检索相似商单 → 达人匹配 → 生成方案。"""
        out = self.r.search(requirement, top_k=top_k)
        ctx, cites = self.r.build_context(out["results"], max_chars=4000)

        # 从需求里粗解析类目，用于达人库检索
        cat = next((c for c in ["美妆", "母婴", "3C数码", "食品饮料", "服饰", "家居", "汽车", "游戏"]
                    if c in requirement), None)
        kols = self.tool_kol_search(category=cat, semantic_query=requirement, top_k=8) if cat else []
        kol_text = "\n".join(
            f"- {k['kol_id']} {k['kol_name']} | {k['tier']} | {k['fans_count']/10000:.1f}w粉 | "
            f"{k['category']}/{k['sub_category']} | 标签{'、'.join(_as_list(k.get('tags')))} | "
            f"刊例¥{k['price_21_60s']:,} | CPM¥{k['cpm']:,.0f} | 均播{k['avg_views']/10000:.1f}w | "
            f"互动率{k['interact_rate']*100:.2f}%" for k in kols) or "（无候选达人）"

        prompt = (f"<context>\n{ctx}\n</context>\n\n"
                  f"<candidate_kols>\n{kol_text}\n</candidate_kols>\n\n"
                  f"【客户需求】\n{requirement}")
        ans = llm.chat([{"role": "system", "content": SYS_PROPOSAL},
                        {"role": "user", "content": prompt}], temperature=0.3, max_tokens=2500)
        ans, val = attach_sources(ans, cites, len(cites))
        return {"proposal": ans, "citations": cites, "candidate_kols": kols,
                "trace": out["info"], "validation": val}

    # ---- 工具：在途商单风险分析 ----
    def followup(self, stagnant_days_medium: int = 3, stagnant_days_high: int = 7,
                 today: "dt.date | None" = None) -> dict:
        """扫描在途商单并生成风险建议。

        today 可注入以复现历史某一天的风险判定（默认取系统当天）。
        早期版本把 today 硬编码为固定日期，会导致「停滞 ≥7 天」的判定随真实时间漂移、
        结论不可复现，故改为可注入参数。
        """
        import datetime as dt
        if today is None:
            today = dt.date.today()
        cur = self.r.pg.execute(
            "SELECT * FROM deal WHERE stage IN ('需求沟通','提案','签约','执行')")
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        enriched = []
        for d in rows:
            upd = dt.date.fromisoformat(str(d["updated_at"])[:10])
            end = dt.date.fromisoformat(str(d["end_date"])[:10])
            sd = (today - upd).days
            overdue = today > end
            level = "高" if (sd >= stagnant_days_high or overdue) else ("中" if sd >= stagnant_days_medium else "正常")
            enriched.append({**d, "stagnant_days": sd, "overdue": overdue, "risk_level": level})
        enriched.sort(key=lambda x: (-{"高": 2, "中": 1, "正常": 0}[x["risk_level"]], -x["stagnant_days"]))
        payload = json_dumps([{k: v for k, v in d.items() if k != "result_metrics"} for d in enriched[:15]])

        try:
            res = llm.chat_json([{"role": "system", "content": SYS_FOLLOWUP},
                                 {"role": "user", "content": payload}], max_tokens=4000)
        except Exception as e:
            res = {"error": str(e), "at_risk": []}
        stat = {"高": sum(1 for x in enriched if x["risk_level"] == "高"),
                "中": sum(1 for x in enriched if x["risk_level"] == "中"),
                "正常": sum(1 for x in enriched if x["risk_level"] == "正常")}
        return {"analysis": res, "stats": stat, "total": len(enriched),
                "raw": enriched[:15]}

    def close(self) -> None:
        self.r.close()


def json_dumps(o) -> str:
    import json
    return json.dumps(o, ensure_ascii=False, indent=1, default=str)


def _as_list(v) -> list:
    """把 JSONB 字段规整为 list（PG 返回的可能已是 list）。"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        import json
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [v]
    return [v]
