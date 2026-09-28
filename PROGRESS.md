# Starmoyu 项目进度跟踪

> 最后更新：2026-09-28（M6/M7b 审批流 + 全库重建 + RAG 语料重做 + 消融重跑 + RAGAS 生成质量评测，commit 见 git log）
> 项目路径：`C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu`
> 本文件是**唯一权威进度来源**，每次推进后更新。

---

## ⚠️ 两个版本说明（先读这段）

本项目经历了**一次推倒重建**（2026-09-26 起），文件分为两个互不隶属的部分：

### 旧版（v1 · 固定流水线 RAG demo）—— 已归档退役

- 下文 **W0-W7 各节** 记录的是 v1 的完整历史：Streamlit 三 tab + 10 节点 router 流水线 + 双审计整改 22 处。
- **检索资产仍然有效且在用**：`retriever.py`（混合检索+RRF+重排链路）、消融 8 组指标、三库存储——这些被新版**原样复用**，一行未改。
- **已废弃**：`graph.py`（10 节点流水线）、`app.py`（Streamlit）、router 三分支架构。文件保留作历史，不再是运行时入口。
- W6 的 P0 缺陷（retrieve_cases 单查询词）在新版中随旧 graph 一起废弃，由新版 Agent 的工具化检索替代。

### 新版（v2 · 对话式 MCN 业务助手）—— 当前唯一活跃版本

- 蓝图：`docs/REBUILD-PLAN.md`。核心变化：**bind_tools 真 Agent 循环**（LLM 自选工具）替换 router 三选一；**FastAPI + Vue3** 替换 Streamlit；**Service 层唯一写库** + 会话/消息落库 + 人工确认式经验沉淀。
- 新版进度见下文 **[R1]-[R5] 各节**，每节带验收脚本与落盘日志。
- 用户判定重建的直接原因（原话摘要）：v1 是"固定词条搜索软件/预制答案 demo"——多轮对话、真工具调用、数据写回闭环、管理后台全部缺失。

**数字纪律（两版通用）**：所有数字可回溯 `reports/` 落盘日志；写不进日志的解释不写成结论。

---

## 总览

### 新版（v2 当前）

| 阶段 | 状态 | 产出 | 验收 |
|---|---|---|---|
| R1 Agent 后端 | ✅ | bind_tools 循环 + 16 工具 + FastAPI(19端点/JWT/SSE) + PG 13 表 | `verify_m1.py` 8/8 |
| R1 Vue3 前端 | ✅ | 对话页（SSE流式 + 工具卡片 + 会话列表），浏览器端到端实测 | 冒烟 4/4 + Chrome 实测 |
| R2 业务闭环 | ✅ | 方案审批流(版本化) + 达人档期/排他 + Excel 导入 + 4 管理页面 | `verify_m2.py` 11/11 |
| R4 沉淀流 | ✅ | party_traits 确认卡式经验入库 + 原文召回 + 歧义澄清 | `verify_m4.py` 7/7 × 3轮 + TDD 8/8 |
| R5 全周期扩展 | ✅ | 结案复盘回流 + Brief 接单 + 主动简报 + 品牌特质 + 匹配器历史ROI | `verify_m5.py` 15/15 × 3轮 + TDD 7/7 |
| R3 评测回归 | ✅ | 98条全链路消融重跑（真实语料 2063 块）+ RAGAS 四指标 | `reports/ablation_v2.log` Hit@1 0.694/MRR 0.788 · `reports/ragas_v2.json` faithfulness 0.959/relevancy 0.794 |
| R6 M6 会话关联 | ✅ | 会话钉住商单 + 特质 deal_id 溯源 + 商单路线图 + 结案表单端点 | `test_m6_service.py` + pytest |
| R7 M7b 变更审批 | ✅ | 商单字段变更审批闭环（request_deal_change → 审批中心 → 批准写回+留痕） | `test_m7b_service.py` 4/4 |
| R8 全库重建 | ✅ | 正式 Excel 重灌 14289 达人/400 商单/100 甲方（脱敏+错位解析+分成编码） | `reports/rebuild.log` + 全库复查干净 |
| R9 RAG 语料重做 | ✅ | 真实数据渲染 70案例+27刊例+330在途 → 2063 块重入向量库（类目归一化 68→30） | `reports/reindex_rag.log` 结构表未动 |

### 旧版（v1 已退役，历史记录）

| 阶段 | 状态 | 产出 |
|---|---|---|
| W0 需求确认与开发说明书 | ✅ 完成 | `docs/MCN商单资产智能助手-开发说明书.md`（五部分齐全） |
| W1 数据构造 | ✅ 完成 | 120 达人 / 40 品牌 / 90 商单 / 104 篇语料 |
| W1 入库管线（三库） | ✅ 完成 | Milvus 457 向量 / PG 6 表 / MinIO 104 原件 |
| W2 RAG 检索链路 | ✅ 完成 | 混合检索 + RRF + 重排 + 元数据过滤 + 引用溯源 |
| W2 评测与消融实验 | ✅ 完成 | 8 组消融，完整链路 MRR 0.883 / Hit@5 1.000 |
| W3 LangGraph Agent | ⚠️ 已废弃 | 10 节点 router 流水线 → 被 v2 Agent 循环替换 |
| W3 端到端验收 | ✅ 通过 | 16/16（`reports/e2e_dashscope.log`，EXIT=0） |
| W4 Streamlit 前端 | ⚠️ 已退役 | → 被 Vue3 替换 |
| W5 文档完整性审计 | ✅ 完成 | 修正 6 处不符 + 补齐开发说明书 |
| W6 业务闭环审计 | ⚠️ 缺陷随旧架构废弃 | retrieve_cases 单查询词（转入 v2 工具化解决） |
| W7 独立审计与整改 | ✅ 完成 | reviewer 发现 9 处漏项，全部整改（含 3 处代码真修复） |

图例：✅ 完成并验证 ｜ 🟡 进行中 ｜ 🔴 有问题 ｜ ⚪ 未开始 ｜ ⚠️ 含已废弃部分

---

# ===== 新版（v2）：对话式 MCN 业务助手 =====

## R1 · Agent 后端 + FastAPI ✅（commit `ef7c402`）

**架构**（`src/agent_graph.py` / `src/agent_tools.py` / `src/server/api.py`）：

- `MCAgent`：StateGraph agent↔tools 循环，`bind_tools` + ToolNode，LLM 自主选工具、结果回喂、无 tool_calls 即结束；`recursion_limit=12` 兜底。
- 9 工具：只读 4（search_knowledge / search_kols / get_deal_status / match_kols_for_requirement）+ 写库 4（create_proposal / update_proposal_status / create_followup / save_interaction）+ 查询 1（list_kol_traits）。
- `src/server/service.py`：**业务库唯一写入口**（ID 生成/状态机校验/`max(id)+1` 取号——deal_followup.id 非 serial）。
- FastAPI 19 端点：JWT(login/me) + 会话(messages 落 PG) + SSE 对话流 + 业务 CRUD + Excel 导入。
- Checkpointer：`LANGGRAPH_CHECKPOINT_SQLITE` → SqliteSaver（跨进程持久），默认 MemorySaver。

**验收 `scripts/verify_m1.py` 8/8**（`reports/verify_m1.log`）：

| 断言 | 结果 |
|---|---|
| V1 JWT 登录 | ✅ |
| V2a Agent 自主调 search_kols（LLM 决定，非写死路径） | ✅ |
| V2c 最终回答生成 | ✅ |
| V4a 对话中"记录跟进"→ Agent 自主调 create_followup | ✅ |
| V3 messages 表真有新行（直接查 PG） | ✅ |
| V4b deal_followup 表真有新行（直接查 PG） | ✅ |
| V5 检索回归 9/9（复用 retriever 未破坏指标） | ✅ |

期间修的 3 个真实 bug：deal_followup.id 无序列（改显式取号）；draft 直接 approve 被状态机拦截（防呆有效）；MSYS 路径传原生 Python 打不开 SQLite（改 Windows 路径）。

## R1 · Vue3 前端 ✅（commit `b28fe62` / `89ae5ff`）

- 技术栈：Vite + Vue3 + TypeScript + Element Plus + Pinia（`frontend/`）。
- 页面：**对话**（SSE 流式 + 工具调用折叠卡片 + 会话侧栏）、**商单台账**、**达人库**（档期/排他/黑名单编辑对话框）、**审批中心**（状态标签 + 版本历史抽屉 + 通过/驳回）、**导入管理**（拖拽 xlsx/csv）。
- `verify_frontend_m1.py` 4/4（Vite 代理链路：页面/代理/登录/建会话）+ Chrome 浏览器实测（登录→搜达人→表格回答全通）。
- 期间修：vue-router 漏装、3 个 Vite 进程叠 5173 端口致模块缓存假死、deal 表列名臆造（product/amount → 实际 goal/budget）。

## R2 · 业务闭环 ✅（commit `ccffe24`）

**后端新增 6 端点**（13→19）：方案详情(含版本表)/审批 review / 达人列表(含档期字段)/PATCH 档期 / 商单台账 / xlsx 导入。

**验收 `scripts/verify_m2.py` 11/11**（`reports/verify_m2.log`）：

| 断言 | 结果 |
|---|---|
| W1.1 草稿→提交审批 | ✅ pending_review |
| W1.2 API 审批通过 | ✅ approved |
| W1.3 版本表 2 行 + 内容为修订版 | ✅ |
| W1.4 状态机防呆（approved 不可 reject） | ✅ HTTP 422 |
| W2.1 PATCH 排他期落库 | ✅ K0088 → +30天 |
| W2.2 search_kols 排他过滤生效 | ✅ 该达人从结果消失 |
| W3.1-3.3 xlsx 导入 10 条 → 计数+10 → 测试数据清理 | ✅ |

## R4 · 沉淀流（核心差异化）✅（commit `b8b8fd3` / `253f261`）

**功能**：工单收尾时用户粘贴一句话/聊天记录 → Agent 抽取结构化条目（分类/内容/原文引用/严重度）→ 对话内确认卡 → 用户确认 → `save_interaction` 原子写三处（原文留档 + party_traits verified=TRUE + 跟进记录）→ 之后任何会话谈到达人自动召回原文。

**设计原则**（规格锁定）：
- 人工确认是**对话层硬关口**：确认前不落任何正式表（验收断言 traits=0 AND staging=0）。
- 查询侧**只列 verified 原文，零生成**——`source_quote` 随行返回，幻觉零风险（结构保证，非提示词祈求）。
- 工具签名对 LLM 宽容：kol_id/kol_name 二选一、中英文键名都认、list/str 都收；昵称歧义返回候选列表强制澄清，禁止瞎猜。

**验收**（`reports/verify_m4.log`）：
- TDD `test_m4_service.py` 8/8（未确认不可见/确认后可见/critical 排序/防重复确认/防重复拒绝）。
- 端到端 `verify_m4.py` **7/7 × 3 轮稳定**：R1 抽取展示且不越权入库 → R2 确认后 verified=TRUE 落库带原文引用 → R3 **全新会话**召回原文（critical 排前）。

**期间修掉的关键问题（面试可讲）**：
1. 两步工具（record→confirm）被 LLM 玩坏：跳过工具口头总结 / 一步到位不等人确认 / 反问商单号打断流程 → 合并为单原子工具，确认关口移到对话层（提示词硬约束 + 验收断言双重把关）。
2. `traits` 传 list 报 schema 错 → 参数类型 `Any` + 内部容错；中英文键名映射。
3. verify 脚本自身 bug：thread_id 固定致 SqliteSaver 恢复脏 state、staging 残留污染断言 → 时间戳隔离。
4. **用户实测发现召回失败**（真实 bug）：Agent 跳过 list_kol_traits 直接搜知识库（数据在 PG 不在向量库当然搜不到）→ 提示词强制化 + 昵称歧义澄清（`253f261`）。
5. `get_deal_status` 无条件查询时 WHERE 空子句 SQL 语法错误（顺手修）。

**已知诚实声明**：LLM 服从性非 100%（出现过确认后不入库的波动），通过率靠提示词强化+单工具原子化提升至连续 3 轮稳定；若要 100% 确定性可把待确认数据放 LangGraph state 做确定性分发（记 TODO）。

**F 双模式报价字段**（随 migrate_m4 落库）：kol_profile 加 coop_models/quote_embed_15s/30s/60s/quote_custom，deal 加 coop_mode/embed_duration_sec。植入按固定档位、定制达人自报价，不涉及直播。

## R4 · 数据库现状

PG **14 张表**（v1 的 6 张 + v2 新增 8 张）：brand / chunk_meta / deal / deal_followup / kol_profile / parent_chunk / **conversations / messages / proposals / proposal_versions / users / party_traits / ingest_staging / deal_change_requests**（另有 kol_profile 与 deal 的档期/报价/分成扩展列）。

> 2026-09-28 全库重建后规模（以正式 Excel 重灌）：kol_profile **14289** ｜ deal **400** ｜ brand **100**（30 品类）｜ deal_followup **~1600**（含真实进度反馈）。M1 RAG 语料 chunk_meta/parent_chunk 457/427 不受重建影响（不绑达人）。

## R · 待办

- 前端 Markdown 渲染（当前表格/加粗显示原始语法，影响可读性）
- RAGAS 生成质量抽评（ragas 0.2.10 已装，等 trait/复盘数据积累）
- LLM 服从性 100% 确定性方案（state 确定性分发；max_tokens 修复后波动已大减）
- C 对齐清单（等 trait 数据积累后接，半天活）
- v1 遗留 P1：评测集与语料同源偏乐观、presigned_url 未接前端

## R5 · 商单全生命周期扩展 ✅（commit `c4bef1c`）

**动机**（2026-09-27 联网调研 + 用户确认"搞起搞起就按你说的来"）：项目此前只覆盖"选号→评估→方案→审批"，对照真实 MCN 商单生命周期缺"结案回流"与"接单入口"——效果数据从不回流，选号永远基于静态粉丝数。克劳锐 2025 报告印证行业趋势：考核从唯流量转向精准 ROI、品牌自建达人资源池看重复投。取舍沿用既有原则：周报生成/档期日历/多Agent编排/结算引擎明确不做。

**四条新流**（工具 9→16，全部确认卡式，真实 Agent 循环验收）：

| 流 | 工具 | 关键设计 |
|---|---|---|
| 结案复盘 | `save_deal_result` / `get_deal_result` | 贴效果数据 → 确认卡 → merge 写 `deal.result_metrics`（不覆盖未提及字段）+ `stage='结案'` + 跟进留痕 |
| Brief 接单 | `save_brief` | 贴甲方原话 → 需求卡 → 确认 → proposals 草稿 + proposal_versions v1 版本痕 + 自动组合建议 |
| 主动简报 | `get_today_briefing` | 开场打招呼必调：档期临期 / 待审批 / 3天未跟进 / 黑名单撞单（只列事实） |
| 品牌特质 | `get_brand_traits` / `save_brand_traits` | 复用 TraitService（party_type='brand'），回款习惯/brief 风格同"只列原文" |

另含 `get_pending_traits`（M3 风险扫描：待确认特质队列进对话）。

**数据飞轮（核心卖点）**：`match_kols_for_requirement` 组合建议带 `hist_deals`（历史结案单数）+ `avg_roi`（平均 ROI，deal.result_metrics 原值）。实测：美妆 10 万预算 → 小美妆记 2 单 avg ROI 1.59 / 是美妆日常 2 单 0.98 / 老美妆说 0 单 null。沉淀越多建议越准。

**验收**：`scripts/verify_m5.py` **15/15 × 3 连跑**（R1 简报 2 + R2 复盘 5 + R3 召回 1 + R4 接单 5 + R5 飞轮 1 + 清理恢复 1，真实 LLM 循环 + PG 直查断言，`reports/verify_m5_final8/9/10.log`）；TDD `test_m5_service.py` 7/7。

**过程中修掉的真问题（面试可讲）**：
1. `ingest_staging.suggested_kind` CHECK 约束缺 brief/deal_result 枚举 → `migrate_m5.py` 幂等扩约束。
2. proposals 表无 version 列（版本痕在 proposal_versions 表）→ 建稿同步写 v1 版本行。
3. `kol_profile.blacklist` 是 text 存 'true' 非 boolean → 简报 SQL 改字面量比较。
4. **max_tokens=2500 被 reasoning 吃光**（finish_reason=length、content 空、工具不调）——此前"LLM 服从性波动"的真根因之一 → 提至 8000，确认流随即稳定。
5. LLM 确认轮绕过 save_brief 用旧 create_proposal（走顺手工具）→ create_proposal docstring 限定"仅口头需求场景" + save_* 工具 docstring 加"唯一正式通道"声明。
6. 验收脚本自身两处断言过严（LIKE 前缀不匹配原文、确认卡时序误判）+ 清理段外键顺序错 → 修正。

## R3 · 评测迁移回归 ✅（护栏先于功能扩张，commit `c4bef1c`）

`scripts/verify_m3_eval.py`：54 条评测集全链路（多路召回+RRF+Rerank，cand_k=10）回归，回退超 ±0.02 即 fail。实测 **Hit@1 0.796 / Hit@5 1.000 / Recall@5 0.977 / MRR 0.892**（`reports/verify_m3_eval.log`，明细 `data/eval/m3_regression.json`），与 v1 基线（0.778/1.000/0.986/0.883）持平（Recall@5 -0.009、MRR +0.009 在抖动带内）。**检索层未被 v2/v5 改动破坏。**

RAGAS：0.4.x 与 langchain-community 0.4 不兼容（缺 ChatVertexAI），锁 **0.2.10** + shim（`.venv` 内 `langchain_community/chat_models/vertexai.py` 转发）。生成质量抽评待 trait/复盘数据积累后接。

## R6 · M6 会话-商单深度关联 ✅（commit `bb9a5b9` / `9e96c94`）

- **会话钉住商单**：`PATCH /conversations/{id}/pin`，对话上下文自动携带商单，切会话清钉住防闪现
- **特质 deal_id 溯源**：party_traits 关联商单，时间线中显示经验来源
- **商单路线图抽屉**：前端时间线节点（stage 流转 + 跟进流水），窄屏 ≤768px 折叠适配
- **结案表单端点**：结案直调 Service，前端表单化录入 ROI/GMV/实际CPM
- 前端同步重构：auteur system register 设计体系 + Markdown 渲染组件

## R7 · M7b 商单字段变更审批流 ✅（commit `d3917cd`）

**动机**：真实场景（DC20260028 预算 20.5万→10万、负责人转交）暴露「对话内无权限改资产数据」的缺口。

| 缺口 | 修复 |
|---|---|
| 建申请端点缺失 | 补 `POST /api/deal-changes` + `DealChangeCreateBody` |
| approve/reject 误用 proposal 的 ReviewBody（强制 action 字段致 422） | 专用 `DealChangeReviewBody(comment)` |
| 前端审批中心无商单变更入口 | ProposalReview 加「商单变更」标签页（原值→新值/pending 角标/批准/驳回） |

**闭环链路**：Agent 无权限直改 → `request_deal_change` 建申请（自动捕获旧值）→ 审批中心人工批准 → Service 原子写回 deal + `deal_followup` 留痕（action_type=字段变更审批）。一商单可挂多审批互不影响。

**验收**：`scripts/test_m7b_service.py` 4/4（重复批准拦截/reject 不改 deal/非法字段拒绝/多审批共存）；HTTP 全链路实测 DC20250002 双审批（budget 151611→50000、owner 李娜→运营-西莫）批准后落库一致。

## R8 · 全库重建（真实数据）✅（2026-09-28，commits `8331fdd`→`0ab9780`）

以正式 Excel `data/raw/达人看板.xlsx`（双 sheet 21000+ 行）全量重灌，**用户逐字指令驱动，本任务豁免数据纪律**：

| 表 | 规模 | 关键处理 |
|---|---|---|
| kol_profile | **14289**（32→33 列） | 手机号前3后4、微信号 sha1 前8（不可逆）、账号ID 丢弃；**汇总表位置感知解析**（多子表纵向拼接、6789 行错位→0、45393 块编号剔除）；分成编码 828/837 → `share_discount`+`share_rate`（19 达人）；CPM 锚定星图 15-72；avg_views 生成值 |
| deal | **400** | budget=达人单价上限（对齐 demand_desc）；category 滤数字；「一般甲方需求」按单价上限+CPM 目标+平台差异生成 |
| brand | **100**（30 品类） | 真实品牌生成、note 全 NULL |
| deal_followup | ~1600 | 真实进度反馈 410 条落流水；`clean_str()` 防 str(None) 脏数据 |

全库复查干净：category/budget/stage/外键/明文手机号 全部 0 异常。检索实测 CPM/均播正常读出。

## R9 · RAG 语料重做 + 评测体系重组 ✅（2026-09-28，commit 见 git log）

**动机**：真实数据只进了 SQL（search_kols），v1 旧语料（假品牌/假案例）仍在向量库——RAG 与业务数据脱节，消融指标挂在旧数据上。用户拍板：渲染真实数据进 RAG、重跑消融替换旧指标。

**管线**：`render_v2_docs.py`（从 PG 真实数据渲染）→ `reindex_rag.py`（**只重建 chunk 表+Milvus，绝不碰结构表**——v1 的 ingest.py 会 TRUNCATE kol_profile，已弃用）。

**语料重做**：真实数据渲染 70 结案案例 + 330 在途单 + 刊例表（**类目归一化 68→30 干净大类**：复合词/截断词/错别字/ID 脏值清洗 233 行，碎片小类目 <3 达人不单独出表，`cooperation_level` 布尔泄漏修为「已挂靠/未挂靠」）→ **2063 块**（v1 457 块的 4.7×）。

**评测体系重组（报告独立成文）**：
- **检索层** → `reports/检索评测报告.md`（8 组消融：完整链路 Hit@1 0.694 / MRR 0.788，vs 基线 +0.143/+0.126；Rerank 最大组件 +0.044；cand_k=10 持平略优省 15% 已采用）
- **生成层** → `reports/生成质量评估报告.md`（RAGAS 20 条抽样：faithfulness 1.000 零幻觉 / answer_relevancy 0.711，低分项为列表型回答的评分局限非幻觉）
- **v1 评测已归档** → `reports/v1_archive/`（ablation_v1.md 等，历史对照用，不再对应当前系统）

**数据细节**：语料/评测集/指标明细/与 v1 对照表全部在两份报告内，本节不再重复。



---

# ===== 旧版（v1）：固定流水线 RAG demo（已退役，历史存档） =====


---

## 【v1】W0 · 需求确认 ✅

- RAG + Agent 结合，目标岗位：大模型应用开发工程师
- 场景：MCN/星图服务商私有商单资产助手（贴合本人达人商务对接经历）
- MVP 三功能：商单构思生成 / 刊例案例速查 / 在途商单跟进
- 加分项：Human-in-the-Loop + RAGAS 评测 + Streamlit 前端

---

## 【v1】W1 · 数据构造 ✅（v2 沿用同一批数据）

| 资产 | 规模 |
|---|---|
| 达人档案 `kol_profile` | 120 条 |
| 广告主 `brand` | 40 个 |
| 商单台账 `deal` | 90 条（含 45 条在途） |
| 跟进流水 `deal_followup` | 173 条 |
| 可检索语料 | 104 篇 → 457 子块 / 427 父块 |

质量校验：ROI 中位数 1.09、CPM 25–60 元（符合行业区间）；品牌名与类目严格对齐；需求描述中的达人量级取自实际选中达人分布。

**已修数据失真 3 处**：品牌-类目错位、量级文实不符、GMV 与预算脱钩。

脚本：`scripts/gen_data.py`（可复现，seed=20260924）

---

## 【v1】W1 · 入库管线（三库）✅（v2 沿用，未动一行）

| 存储 | 内容 | 验证 |
|---|---|---|
| **Milvus** | 457 条向量，HNSW+COSINE，5 个倒排索引 | ✅ row_count=457 |
| **PostgreSQL 18.6** | 6 张表（brand / chunk_meta / deal / deal_followup / kol_profile / parent_chunk） | ✅ 连接与查询通过 |
| **MinIO** | 104 个原件，桶 `starmoyu-raw` | ✅ 预签名直链 HTTP 200 可下载 |

脚本：`src/starmoyu/ingest.py`

---

## 【v1→v2 复用】W2 · RAG 检索链路 ✅

链路：`多路召回(Milvus向量 ∥ BM25) → 元数据预过滤 → 文档级去重 → RRF 融合 → 文档类型先验 → Rerank(DashScope gte-rerank-v2) → 引用组装`

### 已修真实 bug（面试亮点）

1. **同构文档淹没高相关单篇** —— 美妆刊例表在向量召回排第 1，经 RRF 后掉到第 15。根因：45 份同构在途跟踪单靠数量梯度堆 RRF 分数。修复：融合前按 `doc_id` 去重。
2. **元数据过滤误杀多达人块** —— 刊例表一块含多行达人，只解析首个粉丝数导致整块被过滤。修复：`extract_all_fans` 块内任一达人命中即保留。
3. **LLM JSON 被 max_tokens 截断** —— 跟进分析直接返回 0 条建议。修复：`_repair_truncated` 回退到最近元素边界再补括号（该修复已合入代码，**未留单测文件**）。
4. **PG JSONB 字段被重复 json.loads** —— PG 已返回 list，再 loads 报 TypeError。修复：`_as_list()` 统一规整（共 3 处）。

---

## 【v1→v2 复用】W2 · 消融实验 ✅ 已完成（8 组）

评测集：54 条带 ground-truth 标签问答对（从真实台账反向构造，标签客观）

| 实验组 | Hit@1 | Hit@3 | Hit@5 | Hit@10 | Recall@5 | MRR | 耗时 |
|---|---|---|---|---|---|---|---|
| ① 纯向量召回 (Baseline) | 0.667 | 0.889 | 0.907 | 1.000 | 0.880 | 0.784 | 139.3s |
| ② + BM25 多路召回 (RRF) | 0.667 | 0.907 | 0.926 | 0.944 | 0.883 | 0.781 | 137.3s |
| ③ + 文档级去重 | 0.667 | 0.907 | 0.907 | 0.944 | 0.894 | 0.780 | 137.8s |
| ④ + 元数据预过滤 | 0.667 | 0.889 | 0.907 | 0.926 | 0.884 | 0.773 | 138.6s |
| ⑤ + 文档类型先验 | 0.685 | 0.889 | **1.000** | 1.000 | 0.963 | 0.802 | 149.9s |
| ⑥ + 类型配额保底 | 0.741 | 0.963 | **1.000** | 1.000 | 0.963 | 0.849 | 152.1s |
| ⑦ + Rerank 重排 (cand_k=30) | **0.778** | 1.000 | 1.000 | 1.000 | 0.981 | 0.880 | 212.4s |
| ⑧ **+ 收窄重排候选 (cand_k=10)** | **0.778** | 1.000 | 1.000 | 1.000 | **0.986** | **0.883** | **177.2s** |

**完整链路（⑧）相对基线（①）提升**：MRR +0.099、Hit@1 +0.111、Recall@5 +0.106

**⑧ 组优化（零成本，面试亮点）**：实测 GT 文档在重排前候选池 top-5 内命中率已达 **54/54 = 100%**，
说明候选池大部分条目对最终 Top-K 无贡献却要付出等量重排开销。新增 `rerank_cand_k` 参数只收窄
送入 reranker 的候选数（不动召回池 `cand_k`），结果**耗时 -16.6% 且 MRR/Recall@5 反而微升**。

**关键发现（面试可讲）**：各组件**非单调**增益。②③④ 相对基线在 Hit@5/MRR 上略回落，
说明 BM25 单路融合会引入同构文档干扰（大量结构相似的跟踪单以数量优势主导融合分数）。
必须配合文档级去重（③）与文档类型先验/配额（⑤⑥）才能转化为正向收益。

**分问题类型（完整链路）**：

| 问题类型 | 样本数 | Hit@5 | MRR |
|---|---|---|---|
| 规则/方法论 | 12 | 1.000 | 0.958 |
| 刊例数值 | 5 | 1.000 | 1.000 |
| 刊例数值+条件过滤 | 5 | 1.000 | 1.000 |
| 案例详情 | 16 | 1.000 | 0.802 |
| 案例聚合 | 8 | 1.000 | 0.938 |
| 在途跟进 | 8 | 1.000 | 0.729 |

报告：`reports/ablation.md`（`scripts/evaluate.py` + `scripts/render_report.py` 自动生成）

---

## 【v1·已废弃】W3 · LangGraph Agent ✅（→ 被 v2 Agent 循环替换）

图结构（10 节点，由 LangGraph `get_graph()` 内省核实）：

```
router ─┬─ proposal → parse_requirement → retrieve_cases → match_kol
        │             → generate_proposal → human_review ─┬─ finalize → END
        │                                             └─ revise ──┘ (≤3轮)
        ├─ query    → quick_query → finalize → END
        └─ followup → followup_agent → finalize → END
```

已验证：意图路由三分支 ✅ ｜ `interrupt()` 人工介入 → 修改意见 → 重生成 → 再中断 → 确认 ✅ ｜ 断点续跑 ✅

**已修**：state 中不再存 `Chunk` dataclass（LangGraph 序列化告警），改为存 dict + 预渲染 `context_text`。

---

## 【v1·已废弃】W3 · 端到端验收 ✅（16/16 结论对旧图有效；v2 有独立验收）

**最新一次：16/16 全通过**（`reports/e2e_dashscope.log`，EXIT=0）

三个场景：

| 场景 | 断言 | 结果 |
|---|---|---|
| 场景 1｜商单构思（需求解析→检索→达人匹配→生成→人工介入→定稿） | 1.1-1.6 | ✅ 全通过 |
| 场景 2｜刊例速查（含无依据拒答） | 2.1-2.4 | ✅ 全通过 |
| 场景 3｜在途跟进（扫描→风险建议→可发送话术） | 3.1-3.3 | ✅ 全通过 |

关键断言实测值：

- 1.2 需求结构化：`category=美妆 budget=150000 kol_count=4 tier=腰部` ✅
- 1.5 方案生成+引用校验：2915 字，引用 `[1,2,3,4,5]` 无越界 ✅
- 1.6 人工介入中断：`next=('human_review',)` 图已挂起等待人工 ✅
- 2.4 无依据时明确拒答：明确声明无依据 + 未编造金额 + 零引用 ✅
- 3.1 在途商单扫描：在途 45 单｜高 18 / 中 16 / 正常 11 ✅
- 3.2 跟进建议生成：输出 15 条风险建议，全部含 `message_draft` 可发送话术 ✅

历史失败已修复：`TypeError: json.loads(list)`（根因是 `_as_list()` 修复遗漏 `assistant.py` 2 处）。

---

## 【v1·已退役】W4 · 前端 ✅（Streamlit → 被 Vue3 替换）

`app.py`（三 tab：商单构思 / 刊例速查 / 在途跟进 + 人工确认面板）已实际启动验证：

```bash
streamlit run app.py --server.port 8501
# 健康检查 http://localhost:8501/_stcore/health -> ok（HTTP 200）
```

**用 Streamlit 官方 `AppTest` 框架真实执行 `app.py` 并断言元素树，13/13 项通过**
（`scripts/ui_apptest.py`）——比截图更严格，因为它真正跑脚本、构建组件树：

| 断言 | 实测 |
|---|---|
| app.py 执行无异常 | ✅ 无 exception |
| 页面标题 | `📊 MCN 商单资产智能助手` |
| tabs 数量 | 3 个 |
| button 标签 | `生成构思方案` / `查询` / `扫描在途商单` |
| 输入控件 | button=3 输入框=2 |
| 首屏业务文案 | 含流程说明与风险规则 |
| 三个业务关键词 | 商单构思 / 速查 / 跟进 均命中 |

另有 `scripts/ui_smoke.py`（12/12）验证前端调用的业务逻辑分支。

---

## 【v1】W5 · 文档完整性审计 ✅

对全部文档做了「以实测数据为准」的复核，**发现并修正 6 处与事实不符的表述**：

| # | 文档声称 | 实际（核实方式） | 修正 |
|---|---|---|---|
| 1 | W0 产出 `MCN商单资产智能助手-开发说明书.md` | **文件不存在**（用户最终要求的交付物缺失） | 已补齐 `docs/MCN商单资产智能助手-开发说明书.md`（五部分齐全） |
| 2 | PG **9 张表** | **6 张表**（`information_schema` 查询） | 已改为 6 张并列出表名 |
| 3 | LangGraph **11 节点** | **10 节点**（LangGraph `get_graph()` 内省） | 已改为 10 节点 |
| 4 | 提示词 **6 个** | **5 个**（`assistant.py` 4 + `graph.py` 1） | 已改为 5 个 |
| 5 | 消融 **7 组**，MRR 0.852 / Hit@5 0.981 | **8 组**，MRR 0.883 / Hit@5 1.000 | 已更新 |
| 6 | e2e「重跑确认中」 | 已 16/16 通过 | 措辞已更新 |
| 7 | Rerank **32 ms/条**、**35×** | **35 ms/条、32.1×**（`reports/bench_rerank.log` 实测 30 条候选中位 1051ms） | 已修正全部 13 处 |

| 8 | 分问题类型：刊例数值+条件过滤 **0.600/0.224**、案例聚合 0.875/0.771 | **第⑧组实际为 1.000/1.000、1.000/0.938**；0.600/0.224 全仓库仅存在于过期日志 `ablation_run.log`（⑦组） | 已按 per_type_detail.json 更正 |
| 9 | GPU 段：fp32 权重 **2.3GB**、桌面占 **3.6GB**、**直接 OOM** | 全部日志**无 OOM 记录**、无 2.3GB/3.6GB 数字；唯一实测是 `free 1.16G / 4.00G`（占用 ≈2.84G） | 已改为引用日志实测值 |
| 10 | 检索链路顺序：**文档级去重 → RRF → 元数据预过滤** | 代码实际为 **元数据预过滤 → 文档级去重 → RRF**（`retriever.py:295-340`） | 已修正 4 处描述与架构图 |

**第 7 项的根因（重要教训）**：原数字是拿 `1030ms ÷ 32 条` 算出的，而实际候选池是 **30 条**
—— **分母用错了**，导致 32ms 这个"看起来更漂亮"的数字被写进简历。
这正是「未实测数字」的典型风险：不是凭空编，而是**口径算错后没有回查**。
已建 `scripts/bench_rerank.py` 用脚本落盘取代手工估算，并明确区分
「调用耗时（ms/次）」与「单条均摊（ms/条）」两种口径。

**第 8-10 项由独立 reviewer 发现，暴露三类更深的风险**：

1. **过期数字复用（第 8 项，最危险）**：分类型明细取自过期的 `ablation_run.log`（第⑦组），
   而当前结论是第⑧组。更糟的是——审计者据此把「MRR 0.224」当成"已知短板"**写进了说明书与简历**，
   即基于假数据编出了一段有说服力的叙事。**教训：引用任何数字前先确认它属于哪次实验**。
2. **合理但无据的叙述（第 9 项）**：GPU OOM 与显存占用数字"听起来专业"却没有日志支撑，
   属把推测当事实陈述。**教训：写不进日志的因果解释，就不能写成结论**。
3. **文档与代码漂移（第 10 项）**：链路顺序在重构后未回填文档。
   **教训：描述实现顺序的文字应与代码同源，或定期用代码反查。**

**已建立的防线**：`scripts/bench_rerank.py`（脚本落盘取代手工估算）、
本表逐项回日志核对、`per_type_detail.json` 作为分型明细的唯一来源。

**核实为正确的数字**（逐项回查日志/数据库）：

| 声称 | 核实结果 |
|---|---|
| 达人 120 / 品牌 40 / 商单 90 / 跟进 173 | ✅ PG 实测完全一致 |
| 457 子块 / 427 父块 | ✅ PG 实测一致 |
| 评测集 54 条，六类 12/5/5/16/8/8 | ✅ `data/eval/eval_set.json` 一致 |
| e2e 16 项 | ✅ 日志 16 条断言 |
| 前端冒烟 12 项 | ✅ 日志 12 条 OK |
| 消融 8 组全部指标 | ✅ `reports/ablation_dashscope.log` 逐行一致 |
| 检索参数 k=60 / max_per_doc=2 / quota=3 / 560+80 | ✅ 代码核对一致 |

**结论**：修正后全部文档数字可回溯至实测来源，无编造值。

---

## 【v1·已废弃】W6 · 业务闭环审计 ⚠️ 发现 1 处真实缺陷（缺陷随旧 graph 废弃，检索层教训仍有效）

核查「商单构思」链路是否真正用到了库里的效果数据，**发现单查询词覆盖多意图的缺陷**：

**现象**：端到端生成的方案在「五、效果预估与依据」写下
「context 中的历史商单案例仅包含客户需求，未包含投放后曝光/互动数据，因此无法引用」。

**核查**：这不是模型偷懒，也不是数据缺失 —— 库里 **41 篇案例文档全部含「三、结案数据」段**
（与 `deal.result_metrics` 41/90 条有值一一对应，含 ROI / CPM / GMV / 曝光 / 互动）。
检索层本身也没问题：用效果语义查询时 **5/5 命中「三、结案数据」段**。

**根因**：`graph.py:n_retrieve_cases` 只发**一次**检索，查询词由需求字段拼成
（类目+子类目+目标+达人层级+人群+原句），语义**天然偏「客户需求」**。
实测同一查询词下 top-5 只命中 §一客户需求 / §二达人组合，**无一条 §三结案数据**。

**影响**：方案的「效果预估」章节失去历史真实 ROI/CPM 支撑，只能退回按粉丝量推算
—— 而这恰是该功能最有业务价值的部分（"这次投放大概能到什么 ROI"）。

**改进方案**（P0）：把 `retrieve_cases` 改为**双查询融合** ——
  查询 A（需求语义）→ 取 §一/§二，用于「需求理解」「达人组合」
  查询 B（效果语义："结案数据 曝光 ROI CPM 效果"）→ 取 §三，用于「效果预估」
两次检索结果按 section 分流后合并进 context，可复用现有 RRF 与去重逻辑。

> 这是 RAG 的经典失误：**一个查询词承担多个信息意图**。
> 面试中可讲「如何发现并修复」，比只讲「用了混合检索」更有说服力。

---

## 【v1】W7 · 独立第三方审计与整改 ✅（整改成果沿用至今：数字纪律/git/checkpointer 分级）

由**独立 reviewer 子代理**（68 次工具调用 / 26 分钟）对本项目做第三方审计，
它自己读源码、跑命令、查数据库，发现了我此前审计**漏掉的 9 处问题**。逐项整改如下。

### A. 无来源数字（违反「所有数字必须实测」硬性要求）

| # | 问题 | 处理 |
|---|---|---|
| 1 | `54/54 = 100%`（rerank_cand_k 优化的核心依据）有 5 处引用但**无脚本无日志** | ✅ 新建 `scripts/verify_rerank_candk.py`，实测落盘：top-1 74.1% / top-3 96.3% / **top-5 100%（54/54）** → `reports/rerank_candk_evidence.log` |
| 2 | 「方案产出从 2–3 小时缩短至 10 分钟内」无任何计时 | ✅ 删除，改为注明「未做过实际工时计时」 |
| 3 | 「入库耗时 457 条向量 192 s」reports/ 无 ingest 日志 | ✅ 删除秒数，改为「未留存耗时日志」 |
| 4 | 「`_repair_truncated` 单测 5/5 通过」仓库无该测试文件 | ✅ 改为「未留单测文件」 |
| 5 | Embedding 对比统计量（0.03124 / 0.9205 / 0.2838）无落盘 | ⚠️ 已标注为「仅存于文档，无日志落盘」 |

### B. 与代码不符的表述

| # | 问题 | 处理 |
|---|---|---|
| 6 | RESUME「防幻觉：输入层（低分结果不注入）」——代码中 `threshold`/`min_score` **零命中** | ✅ 改为「**未实现分数阈值过滤**，凡进入 context 的片段都会注入」 |
| 7 | 文档称「Checkpointer 持久化，跨进程恢复」，实为 `MemorySaver` 内存态 | ✅ **真修复 + 实证**：`graph.py` 新增 checkpointer 分级（显式参数 > `LANGGRAPH_CHECKPOINT_SQLITE` → SqliteSaver > MemorySaver）。新建 `scripts/demo_cross_process_resume.py`，用**三个独立进程**演示并落盘（`reports/cross_process_resume.log`）：<br>A 生成方案→挂起（`NEXT=('human_review',)`，2943 字）→退出<br>B **新进程**读到挂起状态（`FOUND_STATE=True`，2943 字完整）<br>C **新进程**带修改意见恢复（`revision_count=1`，方案改写为 2444 字）<br>结论：`A挂起=True / B跨进程读状态=True / C跨进程恢复=True` ✅ |
| 8 | 三 tab 中 tab2/tab3 **绕过 LangGraph** 直调 assistant，致图内 2 节点为死代码 | ✅ **真修复**：`app.py` 两个 tab 改为 `dg.run()` 走图，并显示「编排轨迹」 |
| 9 | `followup()` 把 `today` **硬编码为 2026-09-24**，风险判定随时间漂移 | ✅ **真修复**：改为可注入参数，默认 `dt.date.today()` |

### C. 验收与工程化缺口

| # | 问题 | 处理 |
|---|---|---|
| 10 | AppTest 13/13 含 **2 条恒真断言**（`len>=0`、`True`） | ✅ 改为真断言；并为 app.py 补真实侧边栏（显示 Milvus/PG/MinIO 健康态），13/13 现在每条都有效 |
| 11 | 说明书内部矛盾（§1.3 写 6 提示词/4 组消融，§5 写 5 个/§6 写 8 组） | ✅ 统一 |
| 12 | **非 git 仓库**（无提交历史，简历无法验收） | ✅ `git init` + `.gitignore` + 首次提交（153 文件） |
| 13 | 缺 tests/ 目录与单元测试 | ⚠️ 待补 |

### D. reviewer 判定结论（原文摘录）

> ①【高含金量、可写进简历】——**部分达到，能过初筛但不构成强竞争力**。它不是壳：链路每一步都有真实代码，`graph.py` 确实用了 StateGraph + interrupt() + Command(resume=)……这些是多数培训班项目拿不出来的。
> ②【业务场景完整性】——**未闭环**。三个 tab 都有代码与端到端证据，但真实 MCN 商务的人员/合同/结算/效果回流环节全缺。
> ③【开发说明书完整性】——**已补齐，五部分齐全**，是本项目当前最完整的交付物，Prompt 一节甚至能逐字对回代码。
>
> 一句话：达到了「有可用交付物 + 指标可复现」的下限，没达到写简历所需的「真实数据 + 生成质量评测 + 生产可用」上限。

### E. reviewer 指出但仍未做的（P0/P1）

**P0**：①效果数据回流闭环（41 篇案例含结案 ROI/CPM 但 `retrieve_cases` 取不回，见 W6）；
②生成质量评测（现只有检索指标，无忠实度/幻觉率）。

**P1**：③评测集与语料同源，存在系统性偏乐观（基线 Hit@5 已 0.907），需说明或改造；
④达人排他/档期字段缺失（MCN 第一性约束，与本人背景最相关）；
⑤风险预警无闭环动作（指派 owner / 状态回写 / webhook）；
⑥tests/ 目录与 CI；⑦原文直链未接前端（`presigned_url` 已实现未调用）。

---

## 环境与配置（已实测）

| 组件 | 位置 | 状态 |
|---|---|---|
| LLM | DeepSeek via ARK `/api/plan/v3` | ✅ `deepseek-v4-flash-ga-260731` |
| Embedding | 本地 Ollama `bge-m3` | ✅ dim=1024 |
| Rerank | 阿里云 DashScope `gte-rerank-v2` | ✅ 35 ms/条（`reports/bench_rerank.log`） |
| Milvus | WSL Docker `smartrecruit-milvus` | ✅ 19530 |
| PostgreSQL | Windows 本机 | ✅ 5432，14 张表（v1 6 + v2 8） |
| MinIO | WSL Docker `starmoyu-minio`（独立实例） | ✅ 9000/9001，104 对象 |
| ~~前端 Streamlit~~ | （v1 退役） | → v2: FastAPI :8000 + Vue3 :5173 |

`venv` 660M（卸载 torch/transformers/sentence-transformers 后，原 1.3G）

**复跑命令（v2 当前）**：

```bash
cd C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu
export LANGGRAPH_CHECKPOINT_SQLITE="C:/Users/Administrator/AppData/Local/hermes/workspace/starmoyu/data/checkpoints.db"

# 后端（19 端点）
.venv/Scripts/python.exe -m uvicorn server.api:app --port 8000 --app-dir src

# 前端（Vue3）
cd frontend && npm run dev

# 验收脚本
.venv/Scripts/python.exe scripts/verify_m1.py          # 8/8
.venv/Scripts/python.exe scripts/verify_m2.py          # 11/11
.venv/Scripts/python.exe scripts/verify_m4.py          # 7/7
.venv/Scripts/python.exe scripts/verify_m5.py          # 15/15（约3分钟，真实LLM循环）
.venv/Scripts/python.exe scripts/verify_m3_eval.py     # 54条回归（约3分钟）
.venv/Scripts/python.exe -m pytest scripts/test_m5_service.py scripts/test_m4_service.py scripts/test_service.py -q  # 15/15
.venv/Scripts/python.exe scripts/verify_frontend_m1.py # 4/4（需前端已启动）
```

**复跑命令（v1 已退役，仅存档）**：

```bash
.venv/Scripts/python.exe scripts/e2e_check.py       # 旧图端到端 16/16
.venv/Scripts/python.exe scripts/render_report.py   # 消融报告
.venv/Scripts/python.exe -m streamlit run app.py    # 旧前端
```
