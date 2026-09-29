# MCN 商单资产智能助手 · 开发说明书（v2）

> 版本：v2.0（对话式 Agent）｜ 更新：2026-09-29 ｜ 项目路径：`C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu`
> 本文档是**当前唯一活跃版本**的完整开发说明书，覆盖项目背景 / 技术架构 / 核心功能 / 数据结构 / Prompt 设计 / 检索与评测 / 工程化验收 / 已知限制 / 诚实口径九部分。
> **所有数字均来自实测**：数据库实时查询（`information_schema` / 各表 `count(*)`）、落盘日志（`reports/*.log`、`reports/*.json`）、源码核对。无估算值。
>
> ⚠️ **历史说明**：本文替换 v1.0 说明书（当时描述的是已废弃的 Streamlit + 10 节点 router 流水线架构）。v1 的完整历史与废弃原因见 `PROGRESS.md`，v1 实验报告已归档 `reports/archive/`，本文不再重复，也不以 v1 指标作对比基线。

---

## 一、项目背景

### 1.1 业务痛点

MCN / 星图服务商（广告代理商）的核心资产不是达人资源本身，而是**历史商单的沉淀**：报价谈判过程、达人组合效果、执行节奏、踩过的坑。现实中这些资产以散落文档形式存在，导致三个高频问题：

| 痛点 | 具体表现 | 代价 |
|---|---|---|
| **资产不可复用** | 新商单来时，策划凭记忆找「以前类似的单子怎么做」 | 重复造轮子，方案质量取决于个人经验 |
| **刊例与规则查不准** | 达人报价、平台规则、类目方法论散在 100+ 份文档 | 报价口径不一致，商务反复问同事 |
| **在途风险看不见** | 在途商单谁卡住了、卡了几天，靠人肉翻台账 | 超期才发现，客户关系受损 |
| **效果数据不回流** | 结案 ROI/CPM 不沉淀，选号永远基于静态粉丝数 | 历史成败无法指导下一次投放 |

### 1.2 立项思路

**不做通用聊天机器人，做「私域资产 + 可溯源 + 有闭环」的业务助手。** 四条设计原则：

1. **私域优先**：全部知识来自公司内部资产库（刊例 / 历史商单 / 平台规则 / 方法论），不用公网知识——商单报价与客户信息敏感，通用模型不知道这家公司谈过什么价。
2. **可溯源到底**：非结构化检索结论带引用编号；结构化数据直接回 SQL 原值；沉淀的特质只列原文。B 端助手能否被信任，取决于它敢不敢标注来源。
3. **人在环中**：方案、结案、经验沉淀、商单字段变更全部有**确认卡 / 审批关口**，AI 不对资产数据直接写。
4. **真工具调用**：LLM 自主决定「调哪个工具、调几次、何时结束」，而不是固定流水线。

### 1.3 v1 → v2 重建（一句话）

v1 是「固定流水线 RAG demo」（Streamlit 三 tab + 10 节点 router，零写库、无对话、无后台），被判定为不合格。v2 重建为 **LangGraph `bind_tools` 真 Agent + FastAPI + Vue3 + Service 唯一写库**，把「翻历史单 + 拼方案 + 记跟进 + 走审批 + 沉淀经验」收敛进一次对话。检索组件方法论（混合召回 + RRF + Rerank）原样复用，但语料 / 评测集 / 指标全部以 v2 真实数据重建为准。

### 1.4 目标岗位与能力映射

面向 **AI 大模型应用开发工程师（RAG / Agent 方向）**：

| 招聘要求关键词 | 本项目对应实现 |
|---|---|
| RAG 检索增强 | 多路召回(Milvus∥BM25)→RRF→类型先验/配额→去重→Rerank，自建 98 条评测集 + 8 组消融 |
| Agent 编排 | LangGraph StateGraph + `bind_tools` + ToolNode + Checkpointer（SqliteSaver 跨进程持久） |
| 结构化/非结构化双路检索 | 达人/商单走 SQL 精查，语料走向量检索，由 LLM 自主路由 |
| Human-in-the-Loop | 确认卡（对话层）+ 审批流（方案 / 商单字段变更） |
| Rerank 精排 | DashScope `gte-rerank-v2`，含候选裁剪实测决策 |
| 评测体系 | 检索（Hit@k/MRR/Recall）+ 生成质量（RAGAS 四指标） |
| 工程化 | 三存储协同、Service 层唯一写库、里程碑验收脚本、真实数据重建 |

---

## 二、技术架构

### 2.1 总览

```
┌────────────── Vue3 (Vite) 前端 :5173 ──────────────┐
│ 对话(SSE流式+工具卡片) 商单台账 达人库 审批中心 导入  │
└───────────────────────┬────────────────────────────┘
                        │ REST + SSE
┌───────────────────────▼────────────────────────────┐
│              FastAPI 后端 :8000（35 端点）            │
├────────────────────────────────────────────────────┤
│ Agent 层（agent_graph.py）：agent_node ⇄ ToolNode    │
│   LLM bind_tools 自主选工具 · 循环至无 tool_calls     │
│   Checkpointer: SqliteSaver（跨进程对话持久化）       │
├───────────────────────┬────────────────────────────┤
│ RAG 层（retriever.py） │ Service 层（唯一写库者）      │
└───────────┬───────────┴───────────┬────────────────┘
            ▼                       ▼
   Milvus(向量检索)      PostgreSQL(读+写, 14表)   MinIO(原件)
```

### 2.2 技术选型（全部实测可用）

| 层 | 选型 | 运行位置 | 关键实测 |
|---|---|---|---|
| Agent 编排 | LangGraph（StateGraph + `bind_tools` + ToolNode + Checkpointer） | 本地 | 真 Agent 循环，LLM 自主调工具 |
| LLM | DeepSeek（火山方舟 ARK，`deepseek-v4-flash-ga-260731`） | 云端 API | HTTP 200；`max_tokens=8000` 防 reasoning 吃光 |
| Embedding | bge-m3（1024 维） | 本地 Ollama | dim=1024，零 API 成本 |
| Rerank | gte-rerank-v2（阿里云 DashScope） | 云端 API | 35 ms/条（32.1×） |
| 向量库 | Milvus 3.0（HNSW + COSINE + 倒排索引） | 本地 WSL Docker | 2078 条向量（2026-09-29 实测） |
| 关系库 | PostgreSQL 18.6 | 本地 Windows | 14 张表 |
| 对象存储 | MinIO（S3 兼容） | 本地 WSL Docker | 原件 + 预签名直链 |
| 后端 | FastAPI（JWT + SSE 流式） | 本地 :8000 | 35 端点 |
| 前端 | Vue3 + Vite + TS + Element Plus + Pinia | 本地 :5173 | 6 业务页面 |

### 2.3 核心设计：Service 层是唯一写库者

**业务写操作只允许发生在 Service 层**（`src/server/service.py` + `m4/m5/m6/m7b_service.py`），工具函数、API 路由、脚本都只能调 Service。Service 负责：ID 生成（`max(id)+1` 取号，如 `deal_followup.id` 无序列）、状态机校验（方案 `draft→pending_review→approved/rejected`）、版本化、留痕。审计时能一眼断定「数据是谁在什么规则下写的」——这是解决「零沉淀」的根本约束，而非靠提示词。

### 2.4 结构化 / 非结构化双路检索

- **结构化精查**（`search_kols` / `get_deal_status` 等）：14289 达人、400 商单走 **SQL**——类目/量级/粉丝区间/报价上限/排他期过滤是精确过滤问题，向量检索既慢又不准。
- **非结构化召回**（`search_knowledge`）：案例/刊例/方法论/跟踪单 **2068 块**（2026-09-29 实测；消融实验于 2063 块口径）走 **向量 + BM25 混合检索**。
- **路由交给 LLM**：工具 docstring 写清「何时该调我」，模型按用户问题自主决定查哪一路——路由本身是涌现的，不靠穷举分支。

### 2.5 Rerank 选型决策（用数据否掉「看似更专业」的方案）

| 阶段 | 方案 | 实测结果 | 结论 |
|---|---|---|---|
| ① | 本地 CrossEncoder `bge-reranker-v2-m3` | CPU 1126 ms/条，一次消融 60+ 分钟 | 太慢 |
| ② | 同上 GPU（GTX 1650 Ti 4GB） | fp32 OOM；fp16 3225 ms/条（比 CPU 慢 3×，显存颠簸/PCIe 瓶颈） | 硬件限制 |
| ③ | 云端 DashScope `gte-rerank-v2` | **35 ms/条（32.1×）**，且 Hit@1 0.741→0.778 | **采用** |

额外收益：移除 torch/transformers/sentence-transformers 共 3.9GB 依赖，venv 从 1.3G 降到 660M。

---

## 三、核心功能

> **先看全局**：一单商单的端到端闭环总图（知识进入三入口→执行主链→知识回流）见 `PROGRESS.md`「★ 业务闭环总图」。本章按模块拆开讲实现。

### 3.1 Agent 循环（真工具调用）

```python
tools = AGENT_TOOLS + M4_TOOLS + M5_TOOLS        # 19 个
llm = chat_model.bind_tools(tools)
# agent_node: 调 LLM → 返回 AIMessage（可能含 tool_calls）
# should_continue: last.tool_calls 非空 → "tools"，否则 END
# ToolNode 执行 → 结果回喂 LLM → 循环
graph = StateGraph(AgentState) ... .compile(checkpointer=SqliteSaver)
```

- **自主选工具**：用户问价 → 模型自己调 `search_knowledge`；说「记录跟进」→ 自己调 `create_followup`；混合意图（查达人→出方案→记跟进→提审批）一次对话串起来。
- **防失控**：`recursion_limit=25`（初版 12 在真 Agent 多轮连环调用下必爆 `GraphRecursionError`——verify_all F2b 连环工具调用实测暴露，属产品缺陷非测试问题，提额修复）；工具全程 try/except 返回**结构化错误字符串**（LLM 可读可自行纠正后重试），不向对话抛裸异常。
- **工具可见性**：`bind_tools` 必须绑**全部 19 个**——ToolNode 里有实现 ≠ 模型签名可见，漏绑的工具模型只能靠 system prompt 幻觉调用（verify_all F9c 实测暴露：M4/M5 的 11 个工具曾长期漏绑，"验收通过"实为侥幸）。
- **持久化**：`LANGGRAPH_CHECKPOINT_SQLITE` 环境变量指向 sqlite 文件即切 `SqliteSaver`（thread_id=会话 ID，跨进程续跑）；不设兜底 `MemorySaver`。

### 3.2 工具清单（19 个，实测计数：基座 10 + 沉淀 2 + 全周期 7）

| 类 | 工具 | 作用 | 写库 |
|---|---|---|---|
| 基座 | `search_knowledge` | 知识库检索（报价/规则/案例/方法论） | 只读 |
| 基座 | `search_kols` | 达人检索（含排他期/黑名单过滤、CPM/均播） | 只读 |
| 基座 | `get_deal_status` | 商单状态 + 停滞天数 + 风险等级 | 只读 |
| 基座 | `match_kols_for_requirement` | 预算约束组合建议（带历史 ROI） | 只读 |
| 基座 | `create_proposal` | 建方案草稿（仅口头需求场景） | ✅ |
| 基座 | `update_proposal_status` | 审批通过/驳回/修改（版本化） | ✅ |
| 基座 | `create_followup` | 记录跟进动作 | ✅ |
| 基座 | `request_deal_change` | 商单字段变更 → 审批流（不直改） | ✅ |
| 基座 | `sediment_case` | 结案案例沉淀：预览→确认→增量回流 RAG（两次确认） | ✅ |
| 基座 | `save_trait` | 合作画像沉淀 → 确认卡 → `party_traits` verified | ✅ |
| 沉淀 | `save_interaction` | 经验确认卡入库（原子写三处） | ✅ |
| 沉淀 | `list_kol_traits` | 达人特质召回（只列原文） | 只读 |
| 全周期 | `save_deal_result` / `get_deal_result` | 结案复盘归档 / 查询 | ✅ / 读 |
| 全周期 | `save_brief` | Brief 接单 → 建草稿 + 组合建议 | ✅ |
| 全周期 | `get_today_briefing` | 主动简报（临期/待审/失联/撞单） | 只读 |
| 全周期 | `get_brand_traits` / `save_brand_traits` | 品牌特质查询 / 沉淀 | 读 / ✅ |
| 全周期 | `get_pending_traits` | 待确认特质队列 | 只读 |

> **勘误（R10 后更新）**：工具总数 **19**（基座 10 + 沉淀 2 + 全周期 7，源码三列表逐项计数）。`sediment_case`/`save_trait`（R10 新增）入 `AGENT_TOOLS`（8→10）；FastAPI 端点 **35**（R10 沉淀 3 个 + R11 导入三通道/暂存区双审 6 个、识图附件流等）。README/RESUME 已同步。

### 3.3 三条「确认卡」流（核心差异化）

**统一交互契约**：用户给出材料（工单收尾一段话 / 结案数字 / Brief 口头需求）→ Agent 用 LLM 抽取结构化条目 → 对话内渲染**确认卡**（逐条列出、附原文引用）→ 用户回复「确认」（可增删改条目，改后重新出卡）→ 调 `save_*` 工具**原子入库**。

**铁律**：
- **确认前不落任何正式表**（验收断言 `party_traits=0 AND ingest_staging=0`）——抽取失败/用户不确认，除了对话记录什么都不留；
- **查询侧只列 verified 原文，零生成**——`list_kol_traits`/`get_brand_traits` 输出的每个字都能溯源到用户确认过的原文，杜绝「Agent 替用户总结」引入的失真；
- Agent **不重复推销**：提醒一次沉淀后用户不响应即止，不打断业务。

| 流 | 触发时机 | 工具 | 抽取内容 | 原子写（一次事务） |
|---|---|---|---|---|
| 经验沉淀 | 工单收尾/谈判结束 | `save_interaction` | 分类/内容/原文引用/严重度 | 原文留档 + `party_traits`(verified=TRUE) + 跟进记录 |
| 结案复盘 | 结案动作 | `save_deal_result` | ROI/GMV/曝光/互动等数字 | merge 写 `deal.result_metrics`（**不覆盖未提及字段**）+ `stage='结案'` + 跟进留痕 |
| Brief 接单 | 新需求进入 | `save_brief` | 品类/预算/人数/档期/要求 | `proposals` 草稿 + `proposal_versions` v1 版本痕 + 组合建议 |

**实测样例**（R10 DC20250005）：结案数据落库 → Agent 主动提醒 → 预览含 4 条经验（其中「小红书报价 20% 谈价空间」来自跟进流水）→ 确认 → 6 块增量嵌入 → 立即检索命中 top1。

### 3.4 审批流闭环（Human-in-the-Loop 从「方案确认」扩展到「数据变更」）

1. **方案审批（R2）**：`create_proposal`/`save_brief` 产草稿 → 审批中心 → `update_proposal_status`（approve/reject/revise 三动作）。
   - **版本化**：每次 revise 落 `proposal_versions` 新版本，可回溯任意历史版；
   - **状态机防呆**：`draft→submit→pending_review→approve/reject` 单向流转，approved 不可 reject（非法转移 → HTTP 422，实测 verify_all F4e 验证）。
2. **商单字段变更审批（M7b/R7）**：Agent **无权限直改** deal 主字段（预算/负责人/阶段等）→ 调 `request_deal_change` 建申请 → `deal_change_requests` 记录**旧值快照**（审计可对照）→ 审批中心「商单变更」页批准 → Service **原子写回 deal + 跟进流水留痕**，驳回则不动 deal。
   - 一商单可挂多审批，互不影响（实测 26 条申请并存）；
   - 为什么不让 Agent 直改：LLM 数字抽取有非零错误率，主数据变更必须人把关——这是「Agent 提效率，人担责任」的边界。

### 3.5 主动简报与数据飞轮

- **主动简报**（`get_today_briefing`）：开场打招呼必调，汇报四类**事实**（只报查证过的数据，不预测）：① 档期临期 ② 待审批方案 ③ 3 天未跟进 ④ 黑名单撞单。
- **数据飞轮**（读侧）：`match_kols_for_requirement` 组合建议带 `hist_deals`（历史结案单数）+ `avg_roi`（平均 ROI，取 `deal.result_metrics` 原值，**非生成**）。实测：美妆 10 万预算 → 小美妆记 2 单 avg ROI 1.59 / 是美妆日常 2 单 0.98 / 老美妆说 0 单 null（如实返回空，不编数）。
- **结案沉淀闭环（R10）**：结案 → Agent 主动提醒 → 案例预览（`extract_lessons` 从跟进流水提炼拒绝原因/返点/档期/改稿经验，DC20250005 渲染 1080 字含 4 条经验）→ 两次确认 → `ingest_document_incremental` 增量入库（PG 按 `source_file` 先删后插幂等 + Milvus upsert，6 块秒级，**不动存量语料**）→ 知识库即时可检索。前端 DealDrawer 结案表单同步接线（结案成功 → ElMessageBox 提醒 → 预览 → 确认入库）。
- **幂等性实测**：同一案例二次入库总块数稳定（2064→2064），重复结案/误触确认不会污染语料。

### 3.6 图片识图沉淀流（R11，截图 → 经验入库全链路）

**问题**：商务谈判多发生在微信里，经验锁在聊天截图里打不了字也进不了库。v2 早期方案是「诚实降级」（图片只留档、请用户文字补充），R11 升级为**真识图**。

**链路（每步实测）**：

```
用户拖图/粘贴 → ChatView FileReader 转 base64 → 预览条确认
  → POST /api/chat/{conv} attachments[{name, data_base64}]   # 注意 snake_case
  → MinIO 留档 chat-attachments/{conv}/{时间戳}_{i}.png       # 原图可回溯
  → llm.chat_vision(qwen-vl-plus) 完整转写图中对话            # DashScope compatible-mode
  → 转写文本 + 用户文字 拼成 user_text → LangGraph Agent
  → Agent 从转写抽合作特质（付款/排期/内容尺度/沟通偏好，附原文引用）
  → 确认卡 → 用户「确认」→ save_interaction 原子入库
```

**关键选型依据（实测踩坑）**：ARK plan 通道的 `deepseek-v4-flash` 是**纯文本模型**——带 `image_url` 的请求被**静默降级**（不报错，图片被丢弃，prompt_tokens 仅 +104），模型回答「无法识别」。视觉能力必须走 **qwen-vl-plus（DashScope）**：实测 760×1400 截图准确读出「成交 8万8 / 预付 50% / 10月22日20点」。前端字段名也踩过坑：前端内部 camelCase `dataBase64`，Pydantic 契约是 snake_case `data_base64`，不转换必然 422。

**诚实边界**：识图失败（网络/格式）时如实告知用户并请文字补充，**不编造图片内容**；转写与用户口述冲突时（如截图人名 vs 用户给的达人编号）Agent 主动标注不一致等用户裁决。

**错误可见性（连带修复的两个 422）**：
1. FastAPI 校验错误 `detail` 是 `[{loc,msg}...]` 数组，前端 `ElMessage` 直接渲染成 `[object Object]`（用户截图抓到）→ 后端加全局 `RequestValidationError` handler 转可读字符串 + 前端 `_fmt_detail()` 双保险；
2. 前端附件字段 camelCase `dataBase64` vs Pydantic 契约 `data_base64`，不转换必然 422 `Field required`——正是靠修复 1 后弹窗报出真实字段名才定位的（错误可见性救了第二次调试）。

**验收**：拖 `data/raw/import_test/聊天截图-达人A美妆合作谈判.png` + 一句「帮我沉淀这次合作的经验」→ Agent 直接从转写抽 4 条特质（付款 50%/尾款 7 天、档期 20 号后、剧情 3 秒无特写+露出 ≥25 秒、干脆让价 9.8→8.8 万，均附原文引用），并主动标注截图人名（李雯）与用户给定额（李祉默 K03658）不一致。

### 3.7 导入三通道 + Agent/管理员双审（R11）

**问题**：原导入页只有达人库 csv 直导；业务笔记/SOP 等非结构化知识、新商单台账都进不来，且无质检关口。

**三通道**：

| 通道 | 输入 | 暂存 | 确认后落库 |
|---|---|---|---|
| 达人库 | csv（admin 专属） | 不暂存 | `kol_profile`（ON CONFLICT 幂等） |
| 商单台账 | csv 逐行 | `ingest_staging`(kind=import_deal) | `deal`（可先 JSON 编辑框修字段） |
| 非结构化文档 | md/txt/csv 整篇 | `ingest_staging`(kind=import_doc) | `ingest_document_incremental` 切块向量化 |

**双审流程**：上传 → 暂存区（**不落正式表**）→ **Agent 预审**（`m4_service.stage()` 内 LLM 质检：字段缺失/异常值/敏感信息未脱敏/类目合理性 → verdict pass/warn/reject + issues 存 `extracted`）→ 管理员看结论徽标与问题清单逐条**确认/驳回**（reject 强制入库需二次确认弹窗）→ 落库。非结构化文档确认后即时可检索（实测导入「商务谈判 SOP」后检索命中第 2 位）。

**E2E 实测**：正常商单 pass；预算 -20000 脏数据被 Agent 抓出 reject；8 行 csv 全走暂存不直写 deal 表；`ingest_staging.suggested_kind` CHECK 约束同步扩枚举。

**暂存表结构**（`ingest_staging`）：`suggested_kind`（import_deal/import_doc，CHECK 枚举已 ALTER 扩展）/ `raw_payload`（原始行）/ `extracted`（Agent 预审 verdict+issues JSON）/ `status`（pending/confirmed/rejected）。**confirm 商单前前端弹 JSON 编辑框**——管理员可在落库前修正字段，Agent 预审不是唯一关口而是第一道。

**测试素材**：`data/raw/import_test/`（谈判复盘 md / 微信截图 / 8 行台账 csv 含 1 行预算 -20000 脏数据 / 测试剧本）；csv 用 **UTF-8 with BOM**（Windows Excel 按 GBK 解无 BOM 的 UTF-8 会乱码——实测踩坑后重生）。

---

## 四、数据结构

### 4.1 三存储分工

| 存储 | 承载内容 | 实测规模 |
|---|---|---|
| Milvus `dm_chunks` | 子块向量 + 元数据（混合检索 + 过滤） | **2078** 条向量（含 8 孤儿，检索按 doc_id 过滤不碍事） |
| PostgreSQL | 台账 + 会话/消息 + 父子块 + 特质/审批 | 14 张表 |
| MinIO `starmoyu-raw` | 文档原件（预签名直链供「查看原文」） | — |

### 4.2 数据资产规模（2026-09-29 实时 `count(*)` 核实）

| 表 | 规模 | 说明 |
|---|---|---|
| `kol_profile` | **14289** | 33 列；手机号前3后4、微信号 sha1 前8 脱敏；CPM 锚定星图 15-72 |
| `deal` | **400** | budget=达人单价上限（对齐 demand_desc）；结案带 ROI/GMV/实际CPM |
| `brand` | **100**（30 品类） | note 生成内容全删 |
| `deal_followup` | **1613** | 含真实进度反馈（测试运行后实测） |
| `chunk_meta` | **2068 子块** | RAG 语料（结案案例 424（70 单）+ 在途跟踪单 1261 + 刊例 360 + policies/playbook 18 + 导入文档 5 + 27 刊例表 + 规则/方法论 + 导入文档） |
| `parent_chunk` | **1724 父块** | 生成粒度上下文 |
| `deal_change_requests` | 按业务产生 | 一商单多审批 |
| 其余 | `users` / `conversations` / `messages` / `proposals` / `proposal_versions` / `party_traits` / `ingest_staging` | v2 新增会话/方案/特质体系 |

> **勘误**：`README.md §4` 表格仍写「RAG 语料 chunk 457 子块 / 427 父块」——这是 v1 旧值；R9 语料重做后实时 `count(*)` 为 **2063 子块 / 1724 父块**（R9 时点）；2026-09-29 实测已增至 **2068 子块**（含 R10 沉淀与 R11 导入文档回流）。

### 4.3 14 张表清单（`information_schema` 实查）

v1 的 6 张（`brand` / `chunk_meta` / `deal` / `deal_followup` / `kol_profile` / `parent_chunk`）+ v2 新增 8 张（`conversations` / `messages` / `proposals` / `proposal_versions` / `users` / `party_traits` / `ingest_staging` / `deal_change_requests`）。

### 4.4 分块策略（父子双层）

```
文档 → 按 ## 小节切分 → 每小节 1 个父块（完整内容，入 PG parent_chunk）
                      → 小节内按段落切子块（MAX_CHARS=560, OVERLAP=80）
                      → 子块入 Milvus（检索粒度）+ PG chunk_meta（元数据）
                      → 命中子块 expand_to_parent 回溯父块（生成上下文完整）
```

小块检索准（语义聚焦）、大块生成全（上下文完整）。

### 4.5 数据构成（三层，各层可信度不同）

- **真实底座**：达人库 14289 来自正式 Excel（`data/raw/达人看板.xlsx`，双 sheet 21000+ 行）清洗重灌——含位置感知解析（多子表纵向拼接、错位行重定位）、脱敏、分成编码（828/837 → 折扣+分成比例）。
- **业务规则仿真**：商单 400 / 甲方 100 按**真实业务规则构造**（预算=单价上限、CPM 锚定星图行业区间、平台差异、需求卡字段齐备）——不是随机造数，是业务经验的数据化；涉密真实商单本就不该外流。
- **确定性渲染**：70 结案案例由库内 `result_metrics` **确定性渲染**（非 LLM 生成），文档与台账天然一致、引用可验证。

### 4.6 脱敏红线

手机号前3后4、微信号 sha1 前8（不可逆）、账号 ID 丢弃、明文手机号 0；`data/raw/*.xlsx` 永不提交 git（`.gitignore` 已验证拦截）。

---

## 五、Prompt 与工具契约设计

设计原则：**约束写在铁律里，格式写在 Schema 里，判定交给代码。**

### 5.1 System Prompt 设计要点（`src/agent_graph.py`）

- **工具使用时机表**：每个工具一句「何时该调我」，这是 LLM 选对工具的关键。
- **三条确认卡流的硬约束**：「用户确认后【必须立即调用一次 save_*】」「绝不允许只做口头总结」「未经确认禁止调用」——配合验收脚本断言双保险。
- **主动简报强制**：「开场打招呼必须先调 `get_today_briefing`，禁止只回一句问候语」。
- **行为准则**：数字必须原样引用、工具未找到就明说、模糊先追问、只引用 `list_kol_traits` 原文禁止自行总结。

### 5.2 工具 docstring 设计

- **何时使用**：每个工具 docstring 写清触发场景与边界（如 `create_proposal` 明确「仅限没有 brief 原文的口头需求场景」，`save_brief` 声明「唯一正式通道」）——防止 LLM 走顺手工具绕开关口。
- **签名对 LLM 宽容**：`save_interaction` 的 `kol_id`/`kol_name` 二选一、中英文键名都认、`list`/`str` 都收；`traits`/`metrics` 用 `Any` 类型内部容错。昵称歧义返回候选列表**强制澄清**，禁止瞎猜。

### 5.3 防幻觉四层防线

1. **检索层**：混合召回 + rerank，context 质量是前提。
2. **提示层**：数值必须原样引用、context 没有就明确说「未找到依据」。
3. **校验层**：引用越界检测（`validate_citations`）、参考来源由代码确定性渲染、空上下文强制拒答（`enforce_unknown_on_empty`）。
4. **评测层**：RAGAS faithfulness 实测 0.959 背书。

---

## 六、检索链路与评测

### 6.1 检索链路（`retriever.py`）

```
Query → 多路召回(Milvus向量 ∥ BM25) → 元数据预过滤 → RRF 融合(k=60)
      → 文档类型先验 → 类型配额保底(quota=3) → 文档级去重(max_per_doc=2)
      → Rerank(gte-rerank-v2, cand_k=20 / rerank_cand_k=10) → 引用组装
```

关键参数：`cand_k=20`（召回池宽）、`rerank_cand_k=10`（送入 reranker 的候选数）、`max_per_doc=2`、`quota_per_type=3`、RRF `k=60`。

### 6.2 八组消融结果（语料 2063 块 / 评测集 98 条，`reports/ablation_v2.log`）

| 实验组 | Hit@1 | Hit@5 | Hit@10 | Recall@5 | MRR | 耗时 |
|---|---|---|---|---|---|---|
| ① 纯向量召回（基线） | 0.551 | 0.816 | 0.867 | 0.800 | 0.662 | 256s |
| ② + BM25 多路召回（RRF） | 0.633 | 0.898 | 0.898 | 0.885 | 0.738 | 257s |
| ③ + 文档级去重 | 0.633 | 0.898 | 0.908 | 0.885 | 0.740 | 258s |
| ④ + 元数据预过滤 | 0.602 | 0.857 | 0.908 | 0.852 | 0.712 | 258s |
| ⑤ + 文档类型先验 | 0.643 | 0.878 | 0.929 | 0.872 | 0.742 | 261s |
| ⑥ + 类型配额保底 | 0.643 | 0.878 | 0.929 | 0.872 | 0.744 | 256s |
| ⑦ **+ Rerank（完整链路 cand_k=30）** | **0.694** | **0.908** | 0.918 | 0.886 | **0.788** | 364s |
| ⑧ 完整链路 + cand_k=10 | **0.694** | **0.908** | **0.929** | **0.894** | **0.790** | 309s |

**关键结论**：

1. 完整链路相对基线 **Hit@1 +0.143 / MRR +0.126**。
2. BM25 多路召回第二大增益（MRR +0.076），专有名词/编号类查询靠它命中。
3. **Rerank 是最大单组件**（MRR +0.044，0.744→0.788）。
4. cand_k=10 持平略优且省 15% 耗时 → 生产采用。**注意：此结论与上一轮语料相反——检索优化结论是语料相关的，换语料必须重测。**
5. 去重组件有过两轮修复史（见下），去重的价值在于防止 330 张同构跟踪单刷屏，不在提分。

### 6.3 去重组件调试（面试深度素材，`reports/检索评测报告.md §4`）

| 轮次 | 猜的根因 | 结果 |
|---|---|---|
| 第一轮 | `max_per_doc=2` 是 v1 小文档死值，刊例大表 17-19 块被误杀 → 改自适应上限 | 没修对（③ 仍崩至 0.316） |
| 第二轮 | py-spy 抓栈逐块追踪：真根因是 **dedupe 作用在 RRF 融合前**，对两路召回列表做「每 doc 保 2 块、其余丢弃」的 surgery，污染 RRF 融合分 → 正确文档整体沉底 | 移到融合+先验后的候选池（保序+溢出移队尾不丢弃）→ ③ 恢复 0.740 |

**教训**：组件在管线中的位置（融合前还是融合后）比组件参数影响大得多；单例块级追踪比盯聚合数字有效。

### 6.4 生成质量评测（RAGAS 四指标，`reports/ragas_v2.json`）

| 指标 | 数值 | 有效样本 | 解读 |
|---|---|---|---|
| faithfulness（忠实度） | **0.959** | 14/20 | **近零幻觉** |
| answer_relevancy | **0.794** | 20/20 | 相关度良好，低分样本为列表型回答（Embedding 相似度天然偏低） |
| context_precision | 0.705 | 19/20 | 检索排序质量，与 Hit@1≈0.69 量级吻合 |
| context_recall | 0.559 | 14/20 | 聚合型/跨文档问题对齐损耗拉低 |

**口径必读**：faithfulness 部分 NaN 是「回答为拒答/极短」导致无法构造判断陈述（ragas 实现特性），非幻觉；引用须注明「20 条抽样」；context_recall 只统计有 ground_truth 的 14 条。

---

## 七、工程化与验收

### 7.1 里程碑验收脚本（全部实测 exit 0）

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `scripts/verify_m1.py` | Agent 后端（真工具调用 + PG 直查断言） | 8/8 |
| `scripts/verify_m2.py` | 业务闭环（方案审批/排他/xlsx 导入） | 11/11 |
| `scripts/verify_m4.py` | 沉淀流（确认前不入库/确认后召回） | 7/7 × 3 轮 |
| `scripts/verify_m5.py` | 全生命周期（简报/复盘/接单/飞轮） | 15/15 × 3 轮 |
| `scripts/verify_m3_eval.py` | 检索回归 54 条（回退超 ±0.02 即 fail） | Hit@1 0.796 / MRR 0.892 |
| `scripts/test_m7b_service.py` | 变更审批（重复批准拦截/驳回不改/deal 非法字段/多审批） | 4/4 |
| `scripts/test_m4/m5_service.py` | Service 层 TDD | 8/8 + 7/7 |

### 7.2 复现方式（当前有效）

```bash
cd C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu
export LANGGRAPH_CHECKPOINT_SQLITE="C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu/data/checkpoints.db"

# 后端
.venv/Scripts/python.exe -m uvicorn server.api:app --port 8000 --app-dir src
# 前端
cd frontend && npm run dev     # http://localhost:5173

# 检索消融 8 组（约 40 分钟 → reports/ablation_v2.log）
.venv/Scripts/python.exe scripts/evaluate.py
# RAGAS 生成质量（20 条抽样 → reports/ragas_v2.json）
.venv/Scripts/python.exe scripts/ragas_eval.py
# 真实数据重渲染语料 + 重建向量（只动 chunk 表 + Milvus，不碰结构表）
.venv/Scripts/python.exe scripts/render_v2_docs.py
.venv/Scripts/python.exe scripts/reindex_rag.py
```

### 7.3 已修复的真实 Bug（面试素材，节选）

| 现象 | 根因 | 修复 |
|---|---|---|
| 刊例表向量召回第 1、RRF 后掉到第 15 | 45 份同构跟踪单数量梯度堆 RRF 分 | 融合后按 `doc_id` 去重 |
| 刊例表整块被元数据过滤误杀 | 一块多行达人只解析首个粉丝数 | `extract_all_fans` 块内任一命中即保留 |
| ARK reasoning 吃光 max_tokens（finish_reason=length、content 空、工具不调） | 思考链耗尽配额 | max_tokens 2500 → **8000**（所有走 ARK 的脚本一律 ≥8000） |
| 「LLM 服从性波动」真根因 | 同上（配额耗尽，非模型不服从） | 同上 |
| Ollama 连接永久悬起 | Windows `localhost` 解析为 `::1`，Ollama 只监听 IPv4 | 所有服务地址硬编码 `127.0.0.1` |
| 达人召回失败（真实 bug） | Agent 跳过 `list_kol_traits` 直接搜知识库（数据在 PG 不在向量库） | 提示词强制化 + 昵称歧义澄清 |
| 两步工具（record→confirm）被 LLM 玩坏 | 跳过工具口头总结 / 一步到位不确认 | 合并单原子工具，确认关口移到对话层 |
| `deal_followup.id` 无序列 | 普通 integer 无自增 | `max(id)+1` 显式取号 |

---

## 八、已知限制与改进方向

| 限制 | 影响 | 改进方向 | 优先级 |
|---|---|---|---|
| LLM 工具选择非 100% 服从 | 偶发确认后不入库 | 已用 docstring 强化 + 单工具原子化 + 确认卡关口缓解；彻底方案是 LangGraph state 确定性分发 | 有明确路径 |
| 评测集与语料同源 | 存在系统性偏乐观风险 | 评测集**动态反查当前库**生成（语料变评测集跟着重建，不做一次性手工题库） | 已缓解 |
| 前端 Markdown 渲染 | 部分表格/加粗显示原始语法 | 补渲染组件（R6 已部分补 `Md.vue`） | P1 |
| Rerank 依赖外网 API | 断网不可用 | ONNX Runtime 本地部署 | P2 |
| 原文直链未全量接前端 | `presigned_url` 已实现未全面调用 | 前端 CitationPopover 接入 | P2 |
| 无并发与缓存 | 单用户工具级 | 检索缓存、限流 | P2 |

---

## 九、诚实声明与口径

**数据构成（主动讲清）**：三层各层可信度不同——达人库真实 Excel 重灌（真实底座）→ 商单/甲方按真实业务规则构造（业务经验数据化，非随机）→ 案例语料由库内 result_metrics 确定性渲染（可验证一致）。被追问 ROI 分布来源时如实答「按业务规则构造」，不谎称真实。

**评测口径（引用任何数字必须带的上下文）**：

- 消融指标：Hit@1 0.694 / MRR 0.788 ＠ 语料 2063 块、评测集 98 条（与语料同源反查生成）。
- RAGAS 为 20 条抽样结论；faithfulness 部分样本因拒答/极短回答计 NaN，非幻觉。
- context_recall 0.559 仅统计有 ground_truth 的 14 条。
- 语料/评测集一变，上述绝对值即作废——引用时必须带语料规模与样本量。

**本文档数字勘误（R10 后）**：工具数 **19**（基座 10 + 沉淀 2 + 全周期 7）；端点 **29**；RAG 语料 **2064 子块**（2063 + 结案沉淀 DC20250005 6 块增量，Milvus 2076 向量）。其余 14 表、14289/400/100 达人/商单/甲方均与实时库一致。
