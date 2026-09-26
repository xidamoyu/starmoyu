# REBUILD-PLAN：对话式 MCN 业务助手（Agent + FastAPI + Vue3）

> 版本 v1.0 ｜ 2026-09-26 ｜ 执行者：Hermes Agent ｜ 需求方：项目作者
> 目标：把现存的「固定流水线 RAG demo」重建为**对话式、真工具调用、有数据沉淀、有管理后台**的业务助手。
> 原则：所有数字必须实测可复现；每个里程碑有可跑的验收脚本；复用 > 重写。

---

## 0. 问题定义（为什么重建）

已核实的现状缺陷（2026-09-26 复审结论，均有代码证据）：

| # | 缺陷 | 证据 |
|---|---|---|
| 1 | 不是 Agent，是固定流水线 | 无 `bind_tools`/`ToolNode`；条件边仅 router 一条 |
| 2 | 零数据沉淀 | assistant/graph/retriever 零写库；`n_finalize` 只返回一行日志 |
| 3 | 无对话能力 | 无 messages/history，单次请求单次返回 |
| 4 | 前端一次性 | Streamlit 三 tab，无管理入口、无表单 |
| 5 | 数据源一次性 | `ingest.py` 只手工跑一次，业务运行期数据冻死 |

**保留的资产**（已逐文件确认）：`retriever.py` 8 步混合检索（54 条评测集 + 8 组消融指标）、5 个 System Prompt、三存储（Milvus 457 向量 / PG 6 表 / MinIO 104 原件）、`gen_data.py` 仿真数据、LLM 三通道（ARK DeepSeek / Ollama bge-m3 / DashScope rerank）。

**环境**：Windows 11 + git-bash；Node **v24.9.0** / npm **11.6.0**；Python 3.11（.venv）；langgraph 1.2.12（`bind_tools`/`ToolNode`/`SqliteSaver` 已验证可用）。

---

## 1. 目标架构

```
┌────────────────── Vue3 (Vite) 前端 :5173 ──────────────────┐
│  对话主界面 │ 商单工作台 │ 达人库 │ 商单台账 │ 审批中心     │
└──────────────────────────┬──────────────────────────────┘
                           │ REST + SSE
┌──────────────────────────▼──────────────────────────────┐
│                 FastAPI 后端 :8000                        │
│  /api/auth  /api/chat(SSE)  /api/kols  /api/deals        │
│  /api/proposals  /api/admin(导入/重建索引)                │
├─────────────────────────────────────────────────────────┤
│  Agent 层（graph.py 重建）                                │
│   agent_node(LLM+bind_tools) ⇄ ToolNode（循环直到无调用） │
│   工具: search_knowledge │ search_kols │ get_deal_status │
│         match_kols_for_requirement │ create_followup     │
│         create_proposal │ update_proposal_status         │
│   Checkpointer: SqliteSaver（对话历史持久化）             │
├─────────────────────────────────────────────────────────┤
│  RAG 层（retriever.py 原样复用）  │  服务层（新增，唯一写库者）
└──────────────┬───────────────────────────┬──────────────┘
               │                           │
   Milvus(只读检索)        PostgreSQL(读+写)      MinIO(原件)
   dm_chunks               现有6表 + 新增7表       starmoyu-raw
```

**写库规则（核心设计）**：业务写操作**只允许发生在 Service 层**，工具函数通过调用 Service 写库。对话、方案、跟进动作全部落库 —— 解决「零沉淀」。

---

## 2. Agent 设计（真工具调用）

### 2.1 与旧 router 流水线的关系

**替换**。旧 `graph.py` 的 router 三选一删除；意图判断交给 LLM 的工具选择行为本身（用户问价 → LLM 自己会调 `search_knowledge`；用户说"记录跟进" → LLM 自己调 `create_followup`）。人工审批准据保留但移到 **proposal 工作流**（见 2.4）。

### 2.2 工具清单（7 个）

| 工具 | 入参 | 出参 | 写库副作用 |
|---|---|---|---|
| `search_knowledge` | `query: str, doc_type?: str, category?: str` | 引用块列表（含 source_file/section/object_key） | 无（只读检索） |
| `search_kols` | `category?, tier?, fans_min?, fans_max?, price_max?, exclude_conflict_date?` | 达人卡片列表（含排他期状态） | 无 |
| `get_deal_status` | `deal_id?` / `brand_name?` | 商单状态 + 停滞天数 + 风险等级 | 无 |
| `match_kols_for_requirement` | `category, budget, kol_count, kol_tier?` | 预算约束下的组合建议（复用旧 proposal 的组合逻辑） | 无 |
| `create_proposal` | `requirement_text, deal_id?` | proposal_id（**落库**，状态=草稿） | INSERT proposals |
| `update_proposal_status` | `proposal_id, action: approve/reject/revise, comment?` | 新状态（**落库**，版本化） | INSERT proposal_versions + UPDATE proposals |
| `create_followup` | `deal_id, note, action_type` | followup_id（**落库**） | INSERT deal_followup（现有表扩展字段） |

> 工具 docstring 写清「何时该调我」—— 这是 LLM 选对工具的关键。每个工具的错误要返回**结构化错误**（LLM 能读懂并纠正），不是抛异常。

### 2.3 Agent 循环（伪代码）

```python
tools = [search_knowledge, search_kols, ...]
llm = chat_model.bind_tools(tools)

def agent_node(state: AgentState):
    msgs = state["messages"] + state.get("system_extra", [])
    resp = llm.invoke(msgs)                    # LLM 决定调哪个工具
    return {"messages": [resp]}

def should_continue(state) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    return "tools" if last.tool_calls else END

g = StateGraph(AgentState)
g.add_node("agent", agent_node)
g.add_node("tools", ToolNode(tools))           # 并行执行工具调用
g.add_edge(START, "agent")
g.add_conditional_edges("agent", should_continue)
g.add_edge("tools", "agent")                   # 工具结果回给 LLM，循环
checkpointer = SqliteSaver(conn)               # 对话历史持久化，thread_id=会话ID
```

**防失控**：`recursion_limit=12`；工具执行全程 try/except 返回结构化错误；token 预算超限时让 LLM 总结收尾。

### 2.4 方案审批流（保留人机循环的价值）

`create_proposal` 产出的方案是**草稿**，进入待审批列表（前端审批中心）。
用户在界面上 approve/revise → `update_proposal_status` 落库版本。
**这把旧 demo 里唯一的亮点（interrupt 审批）从「演示」变成「业务闭环」**：审批记录可查、版本可回溯。

---

## 3. 数据模型变更（DDL）

现有 6 表不动（brand/chunk_meta/deal/deal_followup/kol_profile/parent_chunk），新增 7 张：

```sql
-- 用户与认证
CREATE TABLE users (
  user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, display_name TEXT, role TEXT DEFAULT 'operator',
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 对话
CREATE TABLE conversations (
  conv_id TEXT PRIMARY KEY, user_id TEXT REFERENCES users,
  title TEXT, created_at TIMESTAMPTZ DEFAULT now(), last_active_at TIMESTAMPTZ
);
CREATE TABLE messages (
  msg_id TEXT PRIMARY KEY, conv_id TEXT REFERENCES conversations,
  role TEXT CHECK (role IN ('user','assistant','tool')),
  content TEXT, tool_calls JSONB, tool_call_id TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 方案（业务沉淀核心）
CREATE TABLE proposals (
  proposal_id TEXT PRIMARY KEY, conv_id TEXT, deal_id TEXT REFERENCES deal,
  requirement_text TEXT NOT NULL, content TEXT NOT NULL,
  status TEXT DEFAULT 'draft' CHECK (status IN ('draft','pending_review','approved','rejected')),
  created_by TEXT REFERENCES users, created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ
);
CREATE TABLE proposal_versions (
  version_id TEXT PRIMARY KEY, proposal_id TEXT REFERENCES proposals,
  version_no INT NOT NULL, content TEXT NOT NULL,
  comment TEXT, changed_by TEXT, created_at TIMESTAMPTZ DEFAULT now()
);

-- 达人档期与排他（用户行业背景的核心约束）
ALTER TABLE kol_profile ADD COLUMN exclusive_until DATE;      -- 排他期截止
ALTER TABLE kol_profile ADD COLUMN available_from DATE;       -- 最早可接档
ALTER TABLE kol_profile ADD COLUMN blacklist TEXT;            -- 黑名单备注

-- 跟进动作增强
ALTER TABLE deal_followup ADD COLUMN action_type TEXT;        -- 电话/约见/催稿/结算...
ALTER TABLE deal_followup ADD COLUMN operator_id TEXT REFERENCES users;
```

**排他冲突检查**（`search_kols` 内置）：若 `exclusive_until >= 目标投放日期` 则过滤并返回 `conflict=true` 标记。这是旧版完全没有的业务约束。

---

## 4. API 设计（REST + SSE）

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| POST | /api/auth/login | 登录发 JWT | 公开 |
| GET | /api/conversations | 会话列表 | JWT |
| POST | /api/conversations | 新建会话 | JWT |
| **POST** | **/api/chat/{conv_id}** | **发消息（SSE 流式返回 agent 过程）** | JWT |
| GET | /api/conversations/{id}/messages | 历史消息 | JWT |
| GET/POST | /api/kols | 达人列表（筛选）/新建 | JWT |
| PATCH | /api/kols/{id} | 更新达人（含档期字段） | JWT |
| GET/POST | /api/deals | 商单列表/新建 | JWT |
| PATCH | /api/deals/{id}/stage | 商单阶段流转 | JWT |
| GET/POST | /api/proposals | 方案列表/创建 | JWT |
| POST | /api/proposals/{id}/review | 审批（approve/reject/revise） | JWT |
| POST | /api/admin/import | Excel/CSV 导入达人/商单 | JWT(admin) |
| POST | /api/admin/reindex | 重建向量索引 | JWT(admin) |

**SSE 协议**（POST /api/chat/{conv_id}，事件流）：
```
event: token      data: {"delta": "..."}            # LLM 生成 token
event: tool_call  data: {"name":"search_kols","args":{...}}   # 让前端展示「正在查达人库…」
event: tool_result data: {"name":"...","summary":"命中5条"}
event: done       data: {"msg_id":"...","proposal_id":null}
```
前端把 `tool_call/tool_result` 渲染为可折叠的过程卡片 —— **Agent 行为可视化**。

---

## 5. 前端设计（Vue3 + Vite）

**技术栈**：Vue 3 + Vite + TypeScript + Pinia + Vue Router + Element Plus（B 端组件库成熟）。

```
frontend/src/
├── api/          # axios 封装 + SSE 客户端
├── stores/       # Pinia: auth / chat / kols / deals
├── views/
│   ├── ChatView.vue        # 对话主界面：消息流 + 过程卡片 + 右侧上下文面板
│   ├── DealWorkbench.vue   # 商单工作台：风险列表 + 跟进记录 + 状态流转
│   ├── KolManage.vue       # 达人库：表格 + 筛选 + 档期/排他编辑
│   ├── ProposalReview.vue  # 审批中心：待审方案 diff + 通过/驳回
│   └── AdminImport.vue     # 管理导入
└── components/
    ├── MessageItem.vue     # 用户/助手消息
    ├── ToolCallCard.vue    # 工具调用过程卡片（Agent 可视化关键组件）
    ├── ProposalCard.vue    # 方案预览（7 段式渲染 + 引用点击看原文）
    └── CitationPopover.vue # 引用溯源弹层（接 MinIO 预签名直链）
```

**设计参考**：使用 `popular-web-designs` skill（Linear/Stripe 风格）定视觉基调；布局细节用 `claude-design` skill 生成一次性的 HTML mockup 供比对。

---

## 6. 复用清单（逐文件）

| 文件 | 处置 | 说明 |
|---|---|---|
| retriever.py | **原样复用** | 8 步链路与指标是项目最有价值的部分 |
| llm.py | 原样复用 | 三通道封装稳定 |
| storage.py | 原样复用 | 连接管理 |
| assistant.py | **拆解** | 4 个 Prompt 迁移到 prompts/ 模块；`tool_kol_search`/`followup` 逻辑迁入工具函数；其余废弃 |
| graph.py | **废弃重写** | 换成 bind_tools 循环（§2.3） |
| app.py | 废弃 | Streamlit 退役（保留目录归档，不删） |
| ingest.py | 复用+扩展 | 加「增量导入」入口供 admin API 调用 |
| gen_data.py / evaluate.py / e2e_check.py | 保留 | 评测体系继续作为回归基线 |
| scripts/bench_*.py / verify_*.py / demo_*.py | 保留 | 实证脚本，重建后需复跑验证检索指标未回退 |

---

## 7. 里程碑

### M1 · 最小可用对话 Agent（3-4 天）★核心
后端：FastAPI 骨架 + JWT 登录 + 7 张新表 DDL + Agent 循环（§2.3）+ 4 个只读工具 + `create_followup`（第一个写库工具）+ SSE 流式。
前端：Vite 项目 + 对话主界面（消息流 + ToolCallCard）+ 登录页。
**验收脚本** `scripts/verify_m1.py`：①登录拿 JWT；②POST 对话「美妆 15 万找 4 个腰部达人」→ SSE 流含 tool_call(search_kols)；③断言 messages 表新增 ≥2 行（用户+助手）；④发「帮我把这个结论记为对 DC20250026 的跟进」→ 断言 deal_followup 新增 1 行；⑤检索指标回归：Hit@1 ≥ 0.75（防复用破坏）。

### M2 · 数据沉淀闭环 + 管理后台（3-4 天）
proposals 全生命周期（草稿→审批→版本化）+ 商单 CRUD + 达人 CRUD + Excel 导入 + 审批中心页 + 达人库/工作台页面。
**验收** `scripts/verify_m2.py`：创建方案→审批通过→版本表 2 行→PATCH 达人档期→再查 `search_kols` 排他生效→导入 10 条达人→计数+10。

### M3 · 业务深化（2-3 天）
`get_deal_status` 风险扫描进对话 + 商单工作台风险列表 + 旧评测 54 条迁移回归 + RAGAS 生成质量抽评（P1-1）。
**验收** `scripts/verify_m3.py`：对话中问「哪些商单卡住了」→ tool_call(get_deal_status) + 返回风险分级；e2e 指标不回退。

**总工期约 8-11 天**。每个里程碑结束 git commit + 验收脚本跑通才算完成。

---

## 8. Skill 使用规划（执行时）

| 阶段 | Skill | 用途 |
|---|---|---|
| M1 前端骨架 | `claude-design` | 生成对话界面 HTML mockup，定视觉基调 |
| M1 前端细化 | `popular-web-designs` | 参考 Linear/Stripe 的卡片/布局模式 |
| M1-M3 全程 | `test-driven-development` | 工具函数与 Service 层先写测试（尤其写库副作用） |
| 调试 | `systematic-debugging` | Agent 循环不收敛/SSE 断流时按流程定位 |
| 技术验证 | `spike` | SSE + LangGraph 流式事件对接不确定时先做一次性验证 |
| 版本管理 | `github` | 里程碑 commit + 远端备份 |
| 交付 | `docx` | 最终把新架构写成简历用说明书 |

---

## 9. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| LLM 工具选择不稳定（错调/漏调） | 高 | 工具 docstring 精写 + 结构化错误返回 + `recursion_limit` 兜底 + 每工具 10 条真实问句的选工具回归集 |
| SSE 与 LangGraph 流式事件对接复杂 | 中 | M1 第一天先做 spike 验证 astream_events 转发可行性 |
| Node 24 + Vite 生态兼容 | 低 | Vite 7 官方支持 Node 20.19+/22.12+，24 无碍；锁定版本 |
| 检索复用时指标回退 | 中 | M1 验收含 Hit@1 ≥ 0.75 回归断言 |
| Windows 前端工具链慢 | 低 | pnpm + 关闭 Defender 实时扫描 node_modules |
| **范围蔓延**（做成大平台） | **高** | 严格按 M1→M2→M3；任何新想法记 TODO 不进当前里程碑 |

---

## 10. Definition of Done

1. `scripts/verify_m1.py / verify_m2.py / verify_m3.py` 全部 exit 0
2. 检索指标回归通过（Hit@1 ≥ 0.75 / MRR ≥ 0.85）
3. 对话含 ≥3 轮多轮 + ≥2 次真实写库（followup + proposal）
4. 审批产生 proposal_versions 记录，前端可回看
5. 达人排他期字段生效（冲突达人被过滤且有标记）
6. 前端 5 个页面可用，登录才能进，无 Streamlit 依赖
7. 全部新数字有落盘证据，文档同步更新（新 README + 简历条目）
```
