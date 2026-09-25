# Starmoyu · MCN 商单资产智能助手

> 基于 **LangGraph + RAG** 的私有商单资产助手：沉淀历史商单/刊例/案例，通过多步 Agent 编排完成
> **需求解析 → 相似商单召回 → 达人匹配 → 构思方案生成 → 人工确认 → 定稿** 全链路，
> 支持**引用溯源**与 **Human-in-the-Loop**。

---

## 1. 技术栈（全部实测可用）

| 层次 | 选型 | 运行位置 | 验证状态 |
|---|---|---|---|
| Agent 编排 | **LangGraph**（StateGraph + Checkpointer + `interrupt()`） | 本地 | ✅ 端到端跑通 |
| LLM | **DeepSeek**（火山方舟 ARK，`deepseek-v4-flash/pro`） | 云端 API | ✅ HTTP 200 |
| Embedding | **bge-m3**（1024 维） | **本地 Ollama** | ✅ dim=1024 |
| Rerank | **gte-rerank-v2**（阿里云 DashScope） | 云端 API | ✅ 35 ms/条（30 条候选 1051ms） |
| 向量库 | **Milvus 3.0**（HNSW + COSINE + 倒排索引） | 本地 WSL Docker | ✅ 457 条向量 |
| 关系库 | **PostgreSQL 18.6** | 本地 Windows | ✅ 6 张表 |
| 对象存储 | **MinIO**（S3 兼容） | 本地 WSL Docker | ✅ 104 原件 + 预签名直链 |
| 前端 | Streamlit | 本地 | ✅ |

### 为什么 Rerank 经历了「本地 → 云端」的迁移

**第一阶段：本地推理（已废弃，但过程可讲）**

Ollama **不支持 rerank**，三条证据：

1. `POST /api/rerank` 返回 **404**，CLI 无任何 rerank 命令
2. 官方模型库不含 `bge-reranker` 系列（CrossEncoder 结构无法通过 Ollama 的 `bert` 后端暴露打分接口）
3. `registry.ollama.ai` 网络超时，`ollama pull` 失败

因此改用 `sentence-transformers` 加载 `bge-reranker-v2-m3`。**实测 CPU 上 1126 ms/条**，
一次 54 题消融需 60+ 分钟 —— 太慢，遂尝试 GPU 加速。

**第二阶段：GPU 加速尝试（实测失败，数据如下）**

| 配置 | 耗时 | 结论 |
|---|---|---|
| CPU fp32（基线） | 1126 ms/条 | 可用但慢 |
| GPU fp32 | 未跑通 | 启动前显存仅 free 1.16G / 4.00G，不足以承载模型 fp32 权重 |
| GPU fp16 | **3225 ms/条** | 比 CPU **慢 3 倍** |

GPU 反而更慢的原因：本机 GTX 1650 Ti 只有 4GB 显存，实测**启动前可用仅 free 1.16G**
（`reports/gpu_rerank_bench.log`，即桌面与系统组件已占用约 2.84G）。
fp16 加载阶段显存被彻底吃满（`free 2.35G → free 0.00G`，`reports/gpu_fp16_bench.log`），
推理时权重被迫在显存与宿主内存间反复搬运，PCIe 带宽成为瓶颈。
**结论：该卡可用显存不足以承载 reranker，属硬件层面的限制。**

**第三阶段：改用云端 Rerank API（当前方案）**

改用阿里云 DashScope `gte-rerank-v2` 后：

| 指标 | 本地 CPU CrossEncoder | DashScope gte-rerank-v2 |
|---|---|---|
| 单条耗时 | 1126 ms | **35 ms（32.1× 更快）** |
| 消融总耗时 | 60+ 分钟 | 212 秒 |
| 本地依赖 | torch + transformers（3.9GB） | **无（venv 从 1.3G 降至 660M）** |
| Hit@1 | 0.741 | **0.778** |
| MRR | 0.852 | **0.880** |

云端方案不仅快 32 倍，**检索质量也更高**（gte-rerank-v2 的中文语义判断优于 bge-reranker-v2-m3
在本机 CPU 上的表现），且彻底移除了 3.9GB 的 torch 依赖。

---

## 2. 存储层分工

```
┌─────────────────────────────────────────────────────────────┐
│                      应用层 (Streamlit)                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│              LangGraph 编排层（10 节点状态机）                │
│  router → parse_requirement → retrieve_cases → match_kol     │
│    → generate_proposal → human_review ─┬─→ finalize → END   │
│                                        └─→ revise ──┘（≤3轮）│
└───────┬──────────────────┬──────────────────┬───────────────┘
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   Milvus      │  │ PostgreSQL 18 │  │     MinIO     │
│  向量+元数据   │  │  关系型台账    │  │   原件 PDF    │
│  dm_chunks    │  │ kol_profile   │  │  docs/**.md   │
│  HNSW+COSINE  │  │ deal / brand  │  │  预签名直链    │
│  5 倒排索引    │  │ chunk_meta    │  │  S3 兼容      │
└───────────────┘  └───────────────┘  └───────────────┘
```

- **Milvus**：语料块向量 + 标量元数据（类目/平台/量级/文档类型），负责语义检索与预过滤
- **PostgreSQL**：达人档案、商单台账、跟进流水、父子块文本，负责 SQL 精确筛选与父块扩展
- **MinIO**：合同/结案报告/刊例原件，支持预签名直链（原件不经应用服务器中转）

---

## 3. 数据资产（W1 产出）

| 资产 | 规模 | 说明 |
|---|---|---|
| 达人档案 | 120 条 | 类目/粉丝量/互动率/转化率/人群画像/评级/刊例价 |
| 广告主 | 40 个 | 品牌名与类目严格对齐 |
| 商单台账 | 90 条 | 含 45 条在途；结案单 ROI 中位数 1.09、CPM 25-60 元 |
| 跟进流水 | 173 条 | 阶段流转记录 |
| 可检索语料 | 104 篇 / 457 块 / 427 父块 | 全部由结构化数据渲染，文档与台账天然一致 |

> 关键设计：**语料由结构化数据渲染生成**，保证「引用溯源」可验证，不会出现文档与台账对不上的假数据。

---

## 4. 检索链路与消融实验

```
Query → 意图路由 → 多路召回（Milvus 向量 ∥ BM25）→ 元数据预过滤
      → 文档级去重 → RRF 融合 → 文档类型先验 → 类型配额保底
      → Rerank（gte-rerank-v2）→ 引用组装
```

评测集：**54 条**带 ground-truth 标签的问答对（从真实数据反向生成，标签客观）

| 实验组 | Hit@1 | Hit@5 | Recall@5 | MRR | 耗时 |
|---|---|---|---|---|---|
| ① 纯向量召回 (Baseline) | 0.667 | 0.907 | 0.880 | 0.784 | 139.3s |
| ② + BM25 多路召回 (RRF) | 0.667 | 0.926 | 0.883 | 0.781 | 137.3s |
| ③ + 文档级去重 | 0.667 | 0.907 | 0.894 | 0.780 | 137.8s |
| ④ + 元数据预过滤 | 0.667 | 0.907 | 0.884 | 0.773 | 138.6s |
| ⑤ + 文档类型先验 | 0.685 | 1.000 | 0.963 | 0.802 | 149.9s |
| ⑥ + 类型配额保底 | 0.741 | 1.000 | 0.963 | 0.849 | 152.1s |
| ⑦ **+ Rerank（完整链路）** | **0.778** | 1.000 | 0.981 | **0.880** | 212.4s |
| ⑧ **+ 收窄重排候选 (cand_k=10)** | **0.778** | 1.000 | **0.986** | **0.883** | **177.2s** |

完整链路相对基线：**MRR +0.099、Hit@1 +0.111、Recall@5 +0.106**

### ⑧ 组：一个「零成本」的工程优化

原设计把召回池的全部 30 条候选都送进 reranker。实测发现
**ground-truth 文档在重排前的候选池 top-5 内命中率已是 54/54 = 100%** ——
意味着候选池中大部分条目对最终 Top-K 毫无贡献，却要付出等量重排开销。

于是新增 `rerank_cand_k` 参数，只收窄送入 reranker 的候选数（不动召回池宽度 `cand_k`）。
结果：**耗时 -16.6%（212.4s → 177.2s），且 MRR / Recall@5 反而微升**。

> 该参数默认从环境变量 `RERANK_CAND_K` 读取，生产默认 10。

> 完整 8 组数据与分问题类型明细见 `reports/ablation.md`（脚本自动生成，无手工誊抄）。

---

## 5. 快速开始

```bash
cd starmoyu
export PYTHONPATH="$PWD/src"

# -1) 安装依赖（前置：uv；Rerank 走云端，无需 torch）
uv venv .venv --python 3.11
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
# 另需本地 embedding 模型：ollama pull bge-m3

# 0) 环境自检（三存储 + 三通道）
.venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'src');from starmoyu import storage,llm;print(storage.health());print(llm.channels_health())"

# 1) 生成数据资产
.venv/Scripts/python.exe scripts/gen_data.py

# 2) 入库（三库写入）
.venv/Scripts/python.exe src/starmoyu/ingest.py

# 3) 消融实验（8 组，云端 rerank 约 3 分钟）
.venv/Scripts/python.exe scripts/evaluate.py
.venv/Scripts/python.exe scripts/render_report.py    # 生成 reports/ablation.md

# 4) 端到端验收（16 项断言）
.venv/Scripts/python.exe scripts/e2e_check.py

# 5) 前端验证 + 启动
.venv/Scripts/python.exe scripts/ui_apptest.py       # 13 项 AppTest 断言
.venv/Scripts/python.exe -m streamlit run app.py     # http://localhost:8501
```

### 依赖服务

| 服务 | 地址 | 凭据 |
|---|---|---|
| Milvus | `127.0.0.1:19530` | 无（WSL Docker） |
| MinIO API | `127.0.0.1:9000` | `starmoyu` / `starmoyu123` |
| MinIO 控制台 | `http://127.0.0.1:9001` | 同上 |
| PostgreSQL | `127.0.0.1:5432` | 见 `.env` |
| Ollama | `http://localhost:11434` | 无 |

---

## 6. 目录结构

```
starmoyu/
├── src/starmoyu/
│   ├── llm.py         # 三通道：DeepSeek(ARK) / Ollama Embed / DashScope Rerank
│   ├── storage.py     # 三存储：Milvus / PostgreSQL / MinIO
│   ├── ingest.py      # 入库管线（父子切分/打标/向量化/三库写入）
│   ├── retriever.py   # 混合检索 + RRF + Rerank + 元数据过滤 + 类型配额
│   ├── assistant.py   # RAG 问答 / 方案生成 / 达人检索 / 跟进分析 + 5 个提示词
│   └── graph.py       # LangGraph 状态机 + 人工介入 + 断点续跑
├── scripts/
│   ├── gen_data.py           # 数据构造（seed=20260924）
│   ├── evaluate.py           # 8 组消融实验
│   ├── render_report.py      # 生成 reports/ablation.md
│   ├── e2e_check.py          # 端到端验收（16 项）
│   ├── ui_apptest.py         # 前端元素级验证（13 项）
│   ├── ui_smoke.py           # 前端业务逻辑冒烟（12 项）
│   ├── compare_embedding.py  # Embedding 方案对比
│   └── probe_*.py            # 环境探测脚本
├── app.py             # Streamlit 前端（三 tab）
├── docs/
│   ├── MCN商单资产智能助手-开发说明书.md   # ★ 标准开发说明书（背景/架构/功能/数据/Prompt）
│   └── RESUME.md                           # 简历条目 + 面试问答
├── reports/           # 实验日志与自动生成的报告
└── .env               # 配置（勿提交）
```

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/MCN商单资产智能助手-开发说明书.md` | **标准开发说明书**：项目背景 / 技术架构 / 核心功能 / 数据结构 / Prompt 设计 |
| `docs/RESUME.md` | 简历条目写法 + 10 组面试问答准备 |
| `reports/ablation.md` | 8 组消融实验报告（脚本自动生成，无手工誊抄） |
| `PROGRESS.md` | 开发进度与验收记录 |
