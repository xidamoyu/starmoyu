# Starmoyu · MCN 商单资产智能助手（v2 · 对话式 Agent）

> 面向 MCN / 星图服务商的私有商单资产助手。核心是一个**真工具调用的对话式 Agent**（LangGraph `bind_tools` + ToolNode，LLM 自主选工具、结果回喂、多轮循环），把「翻历史单 + 拼方案 + 记跟进 + 走审批」的人工流程收敛进一次对话。
> 前端 **Vue3 + FastAPI + SSE**，数据**对话内沉淀、确认卡把关、可追溯**，管理动作全部有**审批流闭环**。

> 本项目经历过一次推倒重建：v1 是「固定流水线 RAG demo」（Streamlit 三 tab + 10 节点 router），被判定为不合格后重建为当前 v2。检索资产（混合检索 + RRF + Rerank + 54 条评测集 + 8 组消融）从 v1 原样复用，见文末「历史消融背景」。

---

## 1. 技术栈（全部实测可用）

| 层次 | 选型 | 运行位置 | 验证状态 |
|---|---|---|---|
| Agent 编排 | **LangGraph**（StateGraph + `bind_tools` + ToolNode + Checkpointer） | 本地 | ✅ 真 Agent 循环，LLM 自主调工具 |
| LLM | **DeepSeek**（火山方舟 ARK，`deepseek-v4-flash`） | 云端 API | ✅ HTTP 200（max_tokens ≥8000 防 reasoning 吃光） |
| Embedding | **bge-m3**（1024 维） | 本地 Ollama | ✅ dim=1024，零 API 成本 |
| Rerank | **gte-rerank-v2**（阿里云 DashScope） | 云端 API | ✅ 35 ms/条（30 条候选 1051ms） |
| 向量库 | **Milvus 3.0**（HNSW + COSINE + 倒排索引） | 本地 WSL Docker | ✅ 457 条向量 |
| 关系库 | **PostgreSQL 18.6** | 本地 Windows | ✅ 14 张表 |
| 对象存储 | **MinIO**（S3 兼容） | 本地 WSL Docker | ✅ 原件 + 预签名直链 |
| 后端 | **FastAPI**（JWT + SSE 流式） | 本地 :8000 | ✅ 26 端点 |
| 前端 | **Vue3 + Vite + TS + Element Plus + Pinia** | 本地 :5173 | ✅ 7 页面 |

---

## 2. 架构

```
┌────────────── Vue3 (Vite) 前端 :5173 ──────────────┐
│ 对话(SSE流式+工具卡片) 商单台账 达人库 审批中心 导入  │
└───────────────────────┬────────────────────────────┘
                        │ REST + SSE
┌───────────────────────▼────────────────────────────┐
│              FastAPI 后端 :8000（26 端点）            │
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

**核心设计：Service 层是唯一写库者。** 业务写操作只允许发生在 Service 层，工具函数通过调用 Service 写库——对话、方案、跟进、沉淀、审批全部落库，解决「零沉淀」。

**沉淀流确认卡（差异化）**：工单收尾时用户粘贴一段话 → Agent 抽取结构化条目 → 对话内确认卡 → 用户确认 → 原子写三处（原文留档 + `party_traits` verified=TRUE + 跟进记录）。确认前不落任何正式表，查询侧只列 verified 原文、零生成。

---

## 3. Agent 能力（8 基座 + 2 沉淀 + 7 全周期 = 真实工具数）

**基座工具（8）**：`search_knowledge`（知识库检索）/ `search_kols`（达人检索·含排他过滤与 CPM/均播）/ `get_deal_status`（商单在途）/ `match_kols_for_requirement`（预算约束组合建议）/ `create_proposal` / `update_proposal_status` / `create_followup`（跟进）/ **`request_deal_change`（商单字段变更→审批流）**

**沉淀流（M4，2）**：`save_interaction`（经验确认卡入库）/ `list_kol_traits`（特质召回）

**全生命周期（M5+M6，7）**：`save_deal_result`/`get_deal_result`（结案复盘）/ `save_brief`（Brief 接单）/ `get_today_briefing`（主动简报）/ `get_brand_traits`/`save_brand_traits`（品牌特质）/ `get_pending_traits`（待确认队列）

**审批流闭环（M7b）**：对话内 Agent **无权限直改**商单主字段（预算/负责人/阶段等），调 `request_deal_change` 建申请（`deal_change_requests`，自动捕获旧值）→ 审批中心「商单变更」标签页显示 → 批准后**自动写回 deal + 跟进流水留痕**，驳回则不动 deal。**一商单可挂多个审批，互不影响。**

---

## 4. 数据资产（2026-09-28 全库重建）

以正式 Excel（`data/raw/达人看板.xlsx`，双 sheet 含「组员达人看板」+「之前达人看板汇总」）全量重灌，含脱敏、汇总表错位位置感知解析、828/837 分成编码解析：

| 资产 | 规模 | 说明 |
|---|---|---|
| 达人档案 `kol_profile` | **14289 条** | 32→33 列；手机号前3后4、微信号 sha1 前8 脱敏；CPM 锚定星图区间 15-72 生成；明文手机号 0 |
| 商单台账 `deal` | **400 条** | budget 对齐需求口径；category 无数字；结案带 ROI/GMV/实际CPM |
| 广告主 `brand` | **100 家**（30 品类） | note 列生成内容全删 |
| 跟进流水 `deal_followup` | **~1600 条** | 含真实进度反馈（随阶段详情显示） |
| 变更审批 `deal_change_requests` | 按业务产生 | 一商单多审批 |
| RAG 语料 chunk | 457 子块 / 427 父块 | M1 文档，不绑达人，不受重建影响 |

> 脱敏红线：Excel 原始数据绝不存原文、绝不提交 git。

---

## 5. 快速开始

```bash
cd starmoyu
export LANGGRAPH_CHECKPOINT_SQLITE="C:/.../starmoyu/data/checkpoints.db"

# 后端（26 端点）
.venv/Scripts/python.exe -m uvicorn server.api:app --port 8000 --app-dir src

# 前端（Vue3）
cd frontend && npm run dev    # http://localhost:5173
```

### 依赖服务

| 服务 | 地址 | 凭据 |
|---|---|---|
| Milvus | `127.0.0.1:19530` | 无（WSL Docker） |
| PostgreSQL | `127.0.0.1:5432` | 见 `.env` |
| MinIO | `127.0.0.1:9000` / `:9001` | `starmoyu` / `starmoyu123` |
| Ollama | `http://localhost:11434` | 无 |

### 验收脚本（v2）

```bash
.venv/Scripts/python.exe scripts/verify_m1.py        # Agent 后端 8/8
.venv/Scripts/python.exe scripts/verify_m2.py        # 业务闭环 11/11
.venv/Scripts/python.exe scripts/verify_m4.py        # 沉淀流 7/7 ×3轮
.venv/Scripts/python.exe scripts/verify_m5.py        # 全生命周期 15/15 ×3轮
.venv/Scripts/python.exe scripts/verify_m3_eval.py   # 检索回归 54条
.venv/Scripts/python.exe -m pytest scripts/test_m7b_service.py -q   # M7b 审批 4/4
```

---

## 6. 历史消融背景（v1 检索资产，现仍复用）

检索链路 `多路召回(Milvus向量 ∥ BM25) → 元数据预过滤 → 文档级去重 → RRF 融合 → 文档类型先验 → 类型配额保底 → Rerank(gte-rerank-v2) → 引用组装`，54 条带 ground-truth 评测集做 8 组消融：

| 实验组 | Hit@1 | Hit@5 | Recall@5 | MRR |
|---|---|---|---|---|
| ① 纯向量召回（基线） | 0.667 | 0.907 | 0.880 | 0.784 |
| ⑧ **完整链路（cand_k=10）** | **0.778** | **1.000** | **0.986** | **0.883** |

完整链路相对基线：**MRR +0.099、Hit@1 +0.111、Recall@5 +0.106**。v2 重建后复测 0.796 / 0.892 / 0.977，与 v1 基线持平（评测集不绑达人名，重建不影响）。

> 完整 8 组数据见 `reports/ablation.md`。Rerank 选型（本地 CPU 1126ms → GPU 失败 → 云端 35ms/条 32×）详见该报告与 `reports/bench_rerank.log`。

---

## 7. 目录结构

```
starmoyu/
├── src/
│   ├── starmoyu/         # LLM三通道 / 三存储 / 检索 / 入库 / assistant
│   ├── agent_graph.py    # 真 Agent 循环（bind_tools + ToolNode + Checkpointer）
│   ├── agent_tools.py    # 基座 8 工具
│   ├── m4_tools.py / m5_tools.py  # 沉淀 + 全生命周期工具
│   └── server/           # FastAPI(api.py) + Service 层(m4/m5/m6/m7b_service.py)
├── frontend/src/         # Vue3 页面 + Pinia stores + api 封装
├── scripts/              # 验收脚本 + 数据重建(import_rebuild.py) + 单测
├── docs/                 # REBUILD-PLAN / 开发说明书 / RESUME
├── reports/              # 实验日志与自动生成报告
├── data/raw/达人看板.xlsx # 原始 Excel（未脱敏，不提交 git）
└── PROGRESS.md           # 开发进度（权威进度源）
```

## 8. 文档索引

| 文档 | 内容 |
|---|---|
| `PROGRESS.md` | **开发进度与验收记录（权威进度源）** |
| `docs/REBUILD-PLAN.md` | v1→v2 重建规划 |
| `docs/MCN商单资产智能助手-开发说明书.md` | 标准开发说明书 |
| `docs/RESUME.md` | 简历条目 + 面试问答（v1 消融背景） |
| `reports/ablation.md` | 8 组消融实验报告（脚本自动生成） |
