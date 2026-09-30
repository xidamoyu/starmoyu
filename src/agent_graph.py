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

from langchain_core.messages import (AIMessage, AIMessageChunk, AnyMessage, HumanMessage,
                                     SystemMessage, ToolMessage)
from langchain_core.runnables import ensure_config
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agent_tools import AGENT_TOOLS  # noqa: E402
from m4_tools import M4_TOOLS  # noqa: E402
from m5_tools import M5_TOOLS  # noqa: E402
from starmoyu import llm  # noqa: E402

MAX_STEPS = 25  # 12 会在复杂多轮连环调用时 GraphRecursionError（verify_all F2b 实测）

SYSTEM_PROMPT = """你是「星图商单助手」，服务 MCN 机构的商单运营与商务同事。

## 可用工具与使用时机
- search_knowledge：查报价/规则/历史案例/方法论（回答事实问题前先查，禁止凭记忆答报价）
- search_kols：搜达人（类目/量级/粉丝区间/价格上限），自动排除档期冲突
- match_kols_for_requirement：用户给出类目+预算+人数时，生成组合建议（排序参考达人历史结案效果）
- get_deal_status：查商单进展与风险；不带参数 = 全量在途风险概览
- create_proposal：用户明确要方案时创建草稿（先拿到检索依据再调）
- update_proposal_status：用户做出审批决定或要求改方案时
- create_followup：用户要求记录跟进动作时
- list_kol_traits：查询某达人的沉淀特质（历史合作经验原文条目）。【强制】用户问"某达人有什么要注意的/靠谱吗/什么脾气/有没有坑"，或任何涉及评估、选择、联系某个具体达人的场景，必须先调 list_kol_traits（kol_id 或 kol_name 传一个即可）再回答。库里有条目就引用原文，没有就明说"暂无沉淀记录"。禁止跳过该工具直接回答。
- save_interaction：用户确认沉淀条目后调用，原子入库（原文留档 + 达人特质 + 跟进记录）
- save_deal_result：结案复盘入库（确认卡动作：先展示效果数字，用户确认后调）
- get_deal_result：查商单结案效果（ROI/GMV/曝光/互动原值）
- save_brief：甲方 brief 解析确认后建方案草稿（确认卡动作）
- get_today_briefing：今日工作简报（档期临期/待审批/失联单/黑名单撞单）
- get_brand_traits / save_brand_traits：品牌特质查询与沉淀（回款习惯/brief 风格，同样只列原文）
- get_pending_traits：待人工确认的特质队列

## 主动简报（重要）
新会话开场用户打招呼（你好/在吗/今天有什么事/该干什么）时，【必须先调 get_today_briefing】，
按返回的四类事项逐条简要汇报（没有的类别直接说无），再问用户想先处理哪件。
禁止开场只回一句问候语。

## 沉淀流（重要——必须严格遵守）
当用户说"记录一下/沉淀/这次合作的情况是…"并粘贴内容时：
1. 先用你自己的理解从原文抽取：每条特质 = 分类(沟通偏好/改稿态度/排期习惯/付款要求/内容尺度/其他) + 内容 + 原文引用 + 严重度(critical/warning/info)
2. 把抽取结果用清单展示给用户，明确说"确认无误请回复'确认'"
3. 用户回复"确认/同意/可以/没问题"等肯定答复后，【必须立即调用一次 save_interaction】：kol_id/kol_name 传达人标识，raw_text 传原始内容原文，traits 传你抽取的特质列表（list 直接传，每条含 分类/内容/原文引用/严重度）
4. 绝不允许：用户确认后只做口头总结而不调 save_interaction。也不允许未经用户确认就调它。
5. 如果不知道达人编号(kol_id)，可以在 save_interaction 里直接传达人昵称(kol_name)。
6. save_interaction 的 deal_id 是可选的：用户没提供商单号就直接留空调工具，不要反问商单号打断流程。

## 结案复盘流
当用户粘贴商单结案/效果数据（含 ROI、GMV、曝光、互动量等数字）时：
1. 抽取效果数字用清单展示（标明商单号），说"确认无误请回复'确认'"
2. 用户确认后【立即调用 save_deal_result】：deal_id 传商单编号，raw_text 传原文，metrics 传效果 dict（中文键可直传，如 {"ROI": 2.1, "GMV": 150000, "曝光": 5000000}）
3. 不知道商单号时先问用户或调 get_deal_status 帮助定位；确认卡里必须让用户看到数字与商单号的对应关系

## 结案沉淀流（重要——主动提醒，两次确认）
save_deal_result / 结案表单成功后会返回带「建议沉淀」的 hint。看到该提示后：
1. 【主动提醒】向用户提议："本单已结案，建议沉淀成案例文档进知识库（会自动提炼跟进流水里的复盘经验），要出预览吗？"——不要等用户自己想到沉淀
2. 用户同意 → 调 sediment_case(deal_id, confirm=false) 出预览，把草稿标题和"过程经验"要点转述给用户，问"内容无误？要补充其他经验吗？确认后入库"
3. 用户确认预览内容（可带补充要点传 extra_lessons，多条用；分隔）→ 调 sediment_case(deal_id, extra_lessons=..., confirm=true) 正式入库
4. 绝不允许：跳过预览直接 confirm=true；用户未同意沉淀就反复推销（提一次即可）
用户主动说"沉淀/复盘一下/存进知识库"时，若商单已结案，直接从第 2 步开始。

## Brief 接单流（重要——必须严格遵守）
当用户粘贴甲方需求/brief 原文要求"建方案/接单/起个提案"时：
1. 解析为需求卡（品类/预算/人数/档期/特殊要求），展示需求卡并以一句"确认无误请回复'确认'"结尾。【这一轮禁止调任何写库工具（save_brief/create_proposal 都不行），也不要额外反问细节打断确认】
2. 用户回复"确认/可以/没问题"等肯定答复后，【必须立即调用 save_brief（而不是 create_proposal）】：raw_text 传 brief 原文，category/budget/kol_count/schedule/requirements 传解析字段
3. save_brief 会自动建草稿并返回达人组合建议；把建议表格转述给用户，说明"草稿已建，可在审批中心查看"

## 行为准则
1. 事实性回答必须基于工具返回的内容；工具未找到就明说，禁止编造报价、粉丝数、商单数据。
2. 数字（报价/预算/粉丝量）必须原样引用工具结果，不得四舍五入或估算。
3. 用户表达模糊时先追问，不要猜着调工具。
4. 回答用中文，简洁专业；给出建议时说明依据来自哪次工具查询。
5. 一个问题可能需要连续调用多个工具（例：先查知识库再看达人再建方案），按需串联。
6. 提到达人历史合作经验时，只引用 list_kol_traits 返回的条目原文，禁止自行总结加工。"""


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], lambda a, b: (a or []) + (b or [])]


def _make_model(thinking: bool = True):
    """用现有 llm.py 的 token-plan 通道构造支持 tool-calling 的模型。

    thinking=False 走关闭思考链的快通道（闲聊/寒暄类首响提速，牺牲复杂推理）。"""
    from langchain_openai import ChatOpenAI

    kwargs = {} if thinking else {"extra_body": {"enable_thinking": False}}
    return ChatOpenAI(
        model=os.environ.get("CHAT_MODEL", "qwen3.8-flash"),
        api_key=os.environ.get('CHAT_API_KEY', '') or os.environ.get('ARK_API_KEY', ''),
        base_url=os.environ.get("CHAT_BASE_URL",
                                "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
        temperature=0.3,
        max_tokens=8000,
        timeout=90,
        max_retries=2,
        **kwargs,
    )


# 只有「明显寒暄/自述」才走轻通道（宁可多挂工具，不可漏挂）：
# 短、且完全不含业务信号词
_CHITCHAT_RE = None
_BIZ_WORDS = ("商单", "达人", "刊例", "结案", "沉淀", "方案", "审批", "预算", "ROI", "roi",
              "DC20", "GMV", "品牌", "笔记", "跟进", "报价", "导入", "台账", "案例", "复盘",
              "查", "找", "推荐", "匹配", "记录", "归档", "回流")


def _is_chitchat(text: str) -> bool:
    t = (text or "").strip()
    if len(t) > 14:
        return False
    if any(w in t for w in _BIZ_WORDS):
        return False
    return t in ("你好", "您好", "hi", "Hi", "hello", "Hello", "嗨", "哈喽", "在吗", "在？",
                 "谢谢", "感谢", "辛苦了", "再见", "早上好", "下午好", "晚上好",
                 "你是谁", "你能干什么", "你能做什么", "能干什么", "能做什么", "帮助",
                 "怎么用", "介绍一下你", "自我介绍")


def _make_checkpointer():
    path = (os.environ.get("LANGGRAPH_CHECKPOINT_SQLITE") or "").strip()
    if path:
        conn = sqlite3.connect(path, check_same_thread=False)
        return SqliteSaver(conn), f"sqlite:{path}"
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver(), "memory"


class MCAgent:
    def __init__(self):
        # bind 全部工具（基座+沉淀+全周期）：只 bind 基座会让模型对 M4/M5 工具
        # 「盲调」（签名不在 schema 里，服从性时灵时不灵——verify_all 实测复现）
        self.model = _make_model().bind_tools(
            list(AGENT_TOOLS) + list(M4_TOOLS) + list(M5_TOOLS))
        # 寒暄/能力自述专用轻通道：不挂 24KB 工具 schema、关思考链，首响最快
        self.model_light = _make_model(thinking=False)
        self.cp, self.cp_kind = _make_checkpointer()
        self.graph = self._build()

    # ---- 节点 ----
    def _agent_node(self, state: AgentState) -> dict:
        msgs = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in msgs):
            msgs = [SystemMessage(content=SYSTEM_PROMPT)] + list(msgs)
        # 选路：首轮寒暄且无钉单/无历史工具调用 → 轻快通道
        last_human = next((m for m in reversed(msgs)
                           if isinstance(m, HumanMessage)), None)
        has_tool_history = any(isinstance(m, ToolMessage) for m in msgs)
        pinned = (ensure_config().get("configurable", {}) or {}).get("pinned_deal_id")
        if (last_human is not None and not has_tool_history and not pinned
                and _is_chitchat(str(last_human.content))):
            resp = self.model_light.invoke(msgs)
        else:
            resp = self.model.invoke(msgs)
        return {"messages": [resp]}

    @staticmethod
    def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    def _build(self):
        all_tools = list(AGENT_TOOLS) + list(M4_TOOLS) + list(M5_TOOLS)
        g = StateGraph(AgentState)
        g.add_node("agent", self._agent_node)
        g.add_node("tools", ToolNode(all_tools,
                                     handle_tool_errors=lambda e: f"工具执行失败: {e}"))
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", self._should_continue, ["tools", END])
        g.add_edge("tools", "agent")  # 结果回喂 LLM → 循环
        return g.compile(checkpointer=self.cp)

    # ---- 对外接口 ----
    @staticmethod
    def _cfg(conv_id: str, pinned_deal_id: str | None = None) -> dict:
        """LangGraph 运行配置。钉住商单走 configurable（LangGraph 保证跨节点/线程传播，
        工具内用 ensure_config() 读取 —— 替代 contextvar（SSE 生成器跨线程上下文不可靠）。"""
        conf: dict = {"thread_id": conv_id}
        if pinned_deal_id:
            conf["pinned_deal_id"] = pinned_deal_id
        return {"configurable": conf, "recursion_limit": MAX_STEPS}

    def chat(self, conv_id: str, user_text: str, pinned_deal_id: str | None = None):
        """同步单轮：返回最终 state（含全部消息）。"""
        cfg = self._cfg(conv_id, pinned_deal_id)
        return self.graph.invoke({"messages": [HumanMessage(content=user_text)]}, cfg)

    def chat_stream(self, conv_id: str, user_text: str,
                    pinned_deal_id: str | None = None):
        """流式：yield 事件字典，供 SSE 转发。

        双模式：messages 模式把 LLM 增量 token 实时推前端（真流式，首字可见
        从整段生成完的 ~14s 降到 ~5s）；updates 模式负责工具事件。
        updates 里的完整 AIMessage 不再重复推（增量已推过）。"""
        cfg = self._cfg(conv_id, pinned_deal_id)
        streamed_any = False   # messages 模式是否推过增量(决定全文兜底)
        for mode, ev in self.graph.stream({"messages": [HumanMessage(content=user_text)]}, cfg,
                                          stream_mode=["messages", "updates"]):
            if mode == "messages":
                chunk, meta = ev
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    streamed_any = True
                    yield {"type": "token", "text": chunk.content, "node": "agent"}
            else:
                for node, update in ev.items():
                    for msg in update.get("messages", []):
                        if isinstance(msg, AIMessage) and msg.tool_calls:
                            for tc in msg.tool_calls:
                                yield {"type": "tool_call", "name": tc["name"],
                                       "args": tc["args"], "node": node}
                        elif isinstance(msg, ToolMessage):
                            yield {"type": "tool_result", "name": getattr(msg, "name", "?"),
                                   "preview": (msg.content or "")[:200], "node": node}
                        elif isinstance(msg, AIMessage) and msg.content and not streamed_any:
                            # 模型不支持增量时的兜底：补发全文
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
