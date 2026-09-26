"""Agent 编排：真工具调用循环（替换旧 router 流水线）。

设计见 docs/REBUILD-PLAN.md §2.3。
- LLM 通过 bind_tools 自主决定调哪个工具、调几次、何时结束
- ToolNode 执行工具，结果回喂 LLM，循环直至无 tool_calls
- SqliteSaver 持久化对话（thread_id = 会话ID），支持跨进程续跑
- recursion_limit 兜底防失控
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Annotated, Literal

from langchain_core.messages import (AIMessage, AnyMessage, SystemMessage,
                                     ToolMessage)
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agent_tools import AGENT_TOOLS  # noqa: E402
from starmoyu import llm  # noqa: E402

MAX_STEPS = 12

SYSTEM_PROMPT = """你是「星图商单助手」，服务 MCN 机构的商单运营与商务同事。

## 可用工具与使用时机
- search_knowledge：查报价/规则/历史案例/方法论（回答事实问题前先查，禁止凭记忆答报价）
- search_kols：搜达人（类目/量级/粉丝区间/价格上限），自动排除档期冲突
- match_kols_for_requirement：用户给出类目+预算+人数时，生成组合建议
- get_deal_status：查商单进展与风险；不带参数 = 全量在途风险概览
- create_proposal：用户明确要方案时创建草稿（先拿到检索依据再调）
- update_proposal_status：用户做出审批决定或要求改方案时
- create_followup：用户要求记录跟进动作时

## 行为准则
1. 事实性回答必须基于工具返回的内容；工具未找到就明说，禁止编造报价、粉丝数、商单数据。
2. 数字（报价/预算/粉丝量）必须原样引用工具结果，不得四舍五入或估算。
3. 用户表达模糊时先追问，不要猜着调工具。
4. 回答用中文，简洁专业；给出建议时说明依据来自哪次工具查询。
5. 一个问题可能需要连续调用多个工具（例：先查知识库再看达人再建方案），按需串联。"""


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], lambda a, b: (a or []) + (b or [])]


def _make_model():
    """用现有 llm.py 的 ARK 通道构造支持 tool-calling 的模型。"""
    import httpx
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash-ga-260731"),
        api_key=os.environ.get("ARK_API_KEY", ""),
        base_url=os.environ.get("ARK_BASE_URL", ""),
        temperature=0.3,
        max_tokens=2500,
        timeout=90,
        max_retries=2,
    )


def _make_checkpointer():
    path = (os.environ.get("LANGGRAPH_CHECKPOINT_SQLITE") or "").strip()
    if path:
        conn = sqlite3.connect(path, check_same_thread=False)
        return SqliteSaver(conn), f"sqlite:{path}"
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver(), "memory"


class MCAgent:
    def __init__(self):
        self.model = _make_model().bind_tools(AGENT_TOOLS)
        self.cp, self.cp_kind = _make_checkpointer()
        self.graph = self._build()

    # ---- 节点 ----
    def _agent_node(self, state: AgentState) -> dict:
        msgs = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in msgs):
            msgs = [SystemMessage(content=SYSTEM_PROMPT)] + list(msgs)
        resp = self.model.invoke(msgs)
        return {"messages": [resp]}

    @staticmethod
    def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("agent", self._agent_node)
        g.add_node("tools", ToolNode(AGENT_TOOLS,
                                     handle_tool_errors=lambda e: f"工具执行失败: {e}"))
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", self._should_continue, ["tools", END])
        g.add_edge("tools", "agent")  # 结果回喂 LLM → 循环
        return g.compile(checkpointer=self.cp)

    # ---- 对外接口 ----
    def chat(self, conv_id: str, user_text: str):
        """同步单轮：返回最终 state（含全部消息）。"""
        cfg = {"configurable": {"thread_id": conv_id},
               "recursion_limit": MAX_STEPS}
        return self.graph.invoke({"messages": [("user", user_text)]}, cfg)

    def chat_stream(self, conv_id: str, user_text: str):
        """流式：yield 事件字典，供 SSE 转发。"""
        cfg = {"configurable": {"thread_id": conv_id},
               "recursion_limit": MAX_STEPS}
        for ev in self.graph.stream({"messages": [("user", user_text)]}, cfg,
                                    stream_mode="updates"):
            for node, update in ev.items():
                for msg in update.get("messages", []):
                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        for tc in msg.tool_calls:
                            yield {"type": "tool_call", "name": tc["name"],
                                   "args": tc["args"], "node": node}
                    elif isinstance(msg, ToolMessage):
                        yield {"type": "tool_result", "name": getattr(msg, "name", "?"),
                               "preview": (msg.content or "")[:200], "node": node}
                    elif isinstance(msg, AIMessage):
                        yield {"type": "token", "text": msg.content, "node": node}
        yield {"type": "done"}


if __name__ == "__main__":
    a = MCAgent()
    print("checkpointer:", a.cp_kind)
    g = a.graph.get_graph()
    print("节点:", sorted(g.nodes))
    # 冒烟：一轮真实对话（会调 LLM）
    st = a.chat("smoke-agent-001", "美妆类目有哪些互动率高的腰部达人？")
    from langchain_core.messages import HumanMessage
    for m in st["messages"]:
        if isinstance(m, (HumanMessage, AIMessage, ToolMessage)):
            label = "TOOL" if isinstance(m, ToolMessage) else type(m).__name__
            print("-", label, (m.content or "")[:80].replace("\n", " "))
