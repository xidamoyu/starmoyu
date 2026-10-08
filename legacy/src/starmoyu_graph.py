"""LangGraph 编排层：状态机 + 人工介入（Human-in-the-Loop）+ 断点续跑。

图结构：
    router ─┬─ proposal → parse_requirement → retrieve_cases → match_kol
            │             → generate_proposal → human_review ─┬─ finalize → END
            │                                              └─ revise ──┘（≤3 轮）
            ├─ query    → quick_query → finalize → END
            └─ followup → followup_agent → finalize → END

人工介入：human_review 节点用 interrupt() 暂停图执行，等待运营确认/修改。

Checkpointer 语义（重要）：
    Checkpointer 的**作用域取决于注入的实现**，本项目按以下优先级选择：
      1) 显式传入 checkpointer 参数（生产建议 SqliteSaver / PostgresSaver）
      2) 环境变量 LANGGRAPH_CHECKPOINT_SQLITE 指向的 SQLite 文件 → SqliteSaver（真持久化，可跨进程恢复）
      3) 兜底 MemorySaver（纯内存）
    **仅在使用 (1)(2) 时才能跨进程恢复**；兜底 MemorySaver 下 interrupt 仍是真实挂起，
    但进程退出即丢失 checkpoint。请勿把内存态描述成「可跨进程恢复」。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starmoyu import llm  # noqa: E402
from starmoyu.assistant import (DealAssistant, SYS_FOLLOWUP, SYS_PROPOSAL,  # noqa: E402
                                SYS_QA, SYS_ROUTER, _as_list, attach_sources,
                                enforce_unknown_on_empty)
from starmoyu.retriever import Retriever  # noqa: E402

MAX_REVISION = 3

SYS_PARSE = """你是 MCN 商单需求解析专家。将自然语言需求抽取为结构化字段。

严格要求：
1. 只输出 JSON，不要任何解释文字
2. 无法确定的字段填 null，禁止编造
3. budget 统一换算为人民币数字（元），"15万" → 150000
4. category 必须从以下选取：美妆/母婴/3C数码/食品饮料/服饰/家居/汽车/游戏

输出 Schema：
{"brand": "广告主名称或 null", "category": "类目", "sub_category": "二级类目或 null",
 "budget": 数字或 null, "kol_count": 整数或 null, "kol_tier": "头部/腰部/尾部/不限",
 "platform": "抖音/小红书/快手/不限", "target_audience": "目标人群或 null",
 "campaign_goal": "带货/种草/曝光/新品发布", "hard_constraints": ["硬性约束"]}"""


class State(TypedDict, total=False):
    query: str
    intent: str
    requirement: dict
    retrieved_cases: list          # 存 dict（可序列化），不存 Chunk dataclass
    context_text: str              # 预渲染好的检索上下文，避免从 dataclass 复原
    candidate_kols: list
    proposal: str
    citations: list
    human_feedback: str
    revision_count: int
    answer: str
    validation: dict
    trace: dict
    followup: dict
    log: Annotated[list, lambda a, b: (a or []) + (b if isinstance(b, list) else [b])]


MAP = None


def _make_checkpointer(explicit=None):
    """按优先级选择 checkpointer：显式参数 > SQLite 环境变量 > 内存兜底。

    只有 SQLite（或显式传入的持久化实现）才具备跨进程恢复能力。
    """
    if explicit is not None:
        return explicit, "explicit"
    path = (os.environ.get("LANGGRAPH_CHECKPOINT_SQLITE") or "").strip()
    if path:
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver

            conn = sqlite3.connect(path, check_same_thread=False)
            return SqliteSaver(conn), f"sqlite:{path}"
        except Exception as e:  # 依赖缺失或路径不可写时降级，但明确告知
            print(f"[warn] SqliteSaver 不可用（{type(e).__name__}: {e}），降级为 MemorySaver")
    return MemorySaver(), "memory"


class StarMoyuGraph:
    def __init__(self, assistant: DealAssistant | None = None, checkpointer=None):
        self.a = assistant or DealAssistant()
        self.r: Retriever = self.a.r
        self.cp, self.cp_kind = _make_checkpointer(checkpointer)
        self.persistent = not self.cp_kind.startswith("memory")
        self.graph = self._build()

    # ---------------------------------------------------------- 节点

    def n_router(self, s: State) -> dict:
        route = self.a.route(s["query"])
        return {"intent": route.get("intent", "query"), "log": [f"router → {route.get('intent')}"]}

    def n_parse_requirement(self, s: State) -> dict:
        try:
            req = llm.chat_json([{"role": "system", "content": SYS_PARSE},
                                 {"role": "user", "content": s["query"]}], max_tokens=600)
        except Exception as e:
            req = {"error": str(e), "raw_query": s["query"]}
        return {"requirement": req, "log": [f"parse_requirement → {json.dumps(req, ensure_ascii=False)[:180]}"]}

    def n_retrieve_cases(self, s: State) -> dict:
        req = s.get("requirement") or {}
        # 用结构化字段拼检索语句，比原句更聚焦
        parts = [req.get("category") or "", req.get("sub_category") or "",
                 req.get("campaign_goal") or "", req.get("kol_tier") or "",
                 req.get("target_audience") or "", s["query"]]
        q = " ".join(p for p in parts if p)
        out = self.r.search(q, top_k=5)
        ctx, cites = self.r.build_context(out["results"], max_chars=4000)
        # 只把可序列化的 dict 放进 state（LangGraph checkpointer 需序列化状态）
        return {"retrieved_cases": [c.to_dict() for c in out["results"]],
                "context_text": ctx, "citations": cites,
                "trace": out["info"], "log": [f"retrieve_cases → {len(out['results'])} 条命中"]}

    def n_match_kol(self, s: State) -> dict:
        req = s.get("requirement") or {}
        budget = req.get("budget")
        kcount = req.get("kol_count") or 5
        # 均摊预算：组合投放中头部贵、尾部便宜，用宽松上限 + 语义排序更贴近真实
        per = (budget / kcount * 0.85) if budget else None
        tier = req.get("kol_tier")
        tier = None if tier in (None, "不限") else tier
        kols = self.a.tool_kol_search(
            category=req.get("category") or "",
            tier=tier,
            budget_per_kol=per,
            budget_scope="avg",
            semantic_query=s["query"],
            top_k=max(kcount, 8),
        )
        # 优先保证「量级」这一客户硬约束，预算作为弹性条件逐级放宽
        if len(kols) < 3 and per is not None:
            kols = self.a.tool_kol_search(category=req.get("category") or "", tier=tier,
                                          budget_per_kol=None, semantic_query=s["query"],
                                          top_k=max(kcount, 8))
        if len(kols) < 3:
            kols = self.a.tool_kol_search(category=req.get("category") or "", tier=None,
                                          budget_per_kol=None, semantic_query=s["query"],
                                          top_k=max(kcount, 8))
        return {"candidate_kols": kols, "log": [f"match_kol → {len(kols)} 位候选（均摊预算上限 {per}）"]}

    def n_generate_proposal(self, s: State) -> dict:
        req = s.get("requirement") or {}
        ctx = self._ctx_from_state(s)
        kols = s.get("candidate_kols") or []
        kol_text = "\n".join(
            f"- {k['kol_id']} {k['kol_name']} | {k['tier']} | {k['fans_count']/10000:.1f}w粉 | "
            f"{k['category']}/{k['sub_category']} | 标签{'、'.join(_as_list(k.get('tags')))} | "
            f"刊例¥{k['price_21_60s']:,} | 互动率{k['interact_rate']*100:.2f}%" for k in kols) or "（无候选达人）"
        fb = s.get("human_feedback")
        fb_block = f"\n\n【人工修改意见（必须遵守）】\n{fb}" if fb else ""
        prompt = (f"<context>\n{ctx}\n</context>\n\n<candidate_kols>\n{kol_text}\n</candidate_kols>\n\n"
                  f"【结构化需求】\n{json.dumps(req, ensure_ascii=False, indent=1)}\n\n"
                  f"【原始需求】\n{s['query']}{fb_block}")
        ans = llm.chat([{"role": "system", "content": SYS_PROPOSAL},
                        {"role": "user", "content": prompt}], temperature=0.3, max_tokens=2500)
        cites = s.get("citations") or []
        ans, val = attach_sources(ans, cites, len(cites))
        n = s.get("revision_count", 0)
        return {"proposal": ans, "answer": ans, "validation": val,
                "log": [f"generate_proposal → 第 {n} 稿，{len(ans)} 字，引用校验 ok={val['ok']}"]}

    def n_human_review(self, s: State) -> Command:
        """人工介入节点：interrupt() 暂停，等待运营确认或提出修改意见。"""
        decision = interrupt({
            "type": "proposal_review",
            "proposal": s.get("proposal", ""),
            "revision_count": s.get("revision_count", 0),
            "instruction": "请确认方案，或输入修改意见（如「达人减少到 3 位，预算压到 8 万」）",
        })
        action = (decision or {}).get("action", "approve")
        if action == "revise" and s.get("revision_count", 0) < MAX_REVISION:
            return Command(goto="revise", update={
                "human_feedback": (decision or {}).get("feedback", ""),
                "revision_count": s.get("revision_count", 0) + 1,
                "log": [f"human_review → 需修改：{(decision or {}).get('feedback','')[:60]}"]})
        return Command(goto="finalize", update={
            "human_feedback": "",
            "log": [f"human_review → 确认通过（action={action}）"]})

    def n_revise(self, s: State) -> Command:
        """按人工意见重生成，回到人工复核节点。"""
        upd = self.n_generate_proposal(s)
        upd["log"] = [f"revise → 按意见重生成（第 {s.get('revision_count',0)} 轮）"]
        return Command(goto="human_review", update=upd)

    def n_quick_query(self, s: State) -> dict:
        out = self.r.search(s["query"], top_k=5)
        ctx, cites = self.r.build_context(out["results"], max_chars=4000)
        if not ctx.strip():
            ans, val = "知识库中未找到相关依据。", {"ok": False, "no_citation": True}
        else:
            ans = llm.chat([{"role": "system", "content": SYS_QA},
                            {"role": "user", "content": f"<context>\n{ctx}\n</context>\n\n【问题】{s['query']}"}],
                           temperature=0.1, max_tokens=1500)
            ans = enforce_unknown_on_empty(ans)
            ans, val = attach_sources(ans, cites, len(cites))
        return {"answer": ans, "citations": cites, "validation": val, "trace": out["info"],
                "log": [f"quick_query → 引用校验 ok={val.get('ok')}"]}

    def n_followup_agent(self, s: State) -> dict:
        res = self.a.followup()
        return {"followup": res, "answer": json.dumps(res.get("analysis", {}), ensure_ascii=False, indent=2),
                "log": [f"followup_agent → 在途 {res['total']} 单，高危 {res['stats']['高']} 中风险 {res['stats']['中']}"]}

    def n_finalize(self, s: State) -> dict:
        return {"log": ["finalize → 输出"]}

    # ---------------------------------------------------------- 路由

    @staticmethod
    def route_by_intent(s: State) -> Literal["proposal", "query", "followup"]:
        i = s.get("intent", "query")
        return i if i in ("proposal", "query", "followup") else "query"

    # ---------------------------------------------------------- 组图

    def _build(self):
        g = StateGraph(State)
        g.add_node("router", self.n_router)
        g.add_node("parse_requirement", self.n_parse_requirement)
        g.add_node("retrieve_cases", self.n_retrieve_cases)
        g.add_node("match_kol", self.n_match_kol)
        g.add_node("generate_proposal", self.n_generate_proposal)
        g.add_node("human_review", self.n_human_review)
        g.add_node("revise", self.n_revise)
        g.add_node("quick_query", self.n_quick_query)
        g.add_node("followup_agent", self.n_followup_agent)
        g.add_node("finalize", self.n_finalize)

        g.set_entry_point("router")
        g.add_conditional_edges("router", self.route_by_intent,
                                {"proposal": "parse_requirement", "query": "quick_query",
                                 "followup": "followup_agent"})
        g.add_edge("parse_requirement", "retrieve_cases")
        g.add_edge("retrieve_cases", "match_kol")
        g.add_edge("match_kol", "generate_proposal")
        g.add_edge("generate_proposal", "human_review")
        g.add_edge("quick_query", "finalize")
        g.add_edge("followup_agent", "finalize")
        g.add_edge("finalize", END)
        return g.compile(checkpointer=self.cp)

    # ---------------------------------------------------------- 执行入口

    @staticmethod
    def config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def run(self, query: str, thread_id: str = "t1") -> dict:
        """执行到结束或人工介入点。"""
        state = {"query": query, "revision_count": 0, "log": []}
        out = self.graph.invoke(state, self.config(thread_id))
        return out

    def resume(self, thread_id: str, action: str = "approve", feedback: str = "") -> dict:
        """人工确认后恢复执行。"""
        return self.graph.invoke(Command(resume={"action": action, "feedback": feedback}),
                                 self.config(thread_id))

    def state_of(self, thread_id: str) -> dict:
        return self.graph.get_state(self.config(thread_id)).values

    def _ctx_from_state(self, s: State) -> str:
        """优先用预渲染的上下文（可序列化路径）。"""
        ctx = s.get("context_text")
        if ctx:
            return ctx
        # 兼容：若无预渲染上下文，用 doc_id 回查父块
        ids = [c.get("chunk_id") for c in (s.get("retrieved_cases") or []) if c.get("chunk_id")]
        if not ids:
            return ""
        return self.r.expand_to_parent(ids)[:4000]


if __name__ == "__main__":
    dg = StarMoyuGraph()
    print("图节点:", list(dg.graph.get_graph().nodes.keys()))
    print("边数量:", len(dg.graph.get_graph().edges))
