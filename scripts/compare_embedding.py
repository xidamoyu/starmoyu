"""Embedding 质量对比：本地 Ollama bge-m3 vs 阿里云 DashScope text-embedding-v3。

用同一批真实语料与同一套查询，比较：
  1. 向量维度与数值分布（检测是否被量化）
  2. 语义区分度：正例对的相似度 vs 随机对的相似度（间隔越大越好）
  3. 实际检索效果：在真实语料上跑 Recall@5 / MRR
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = _env()


def ollama_embed(texts: list[str]) -> np.ndarray:
    out = []
    for i in range(0, len(texts), 16):
        r = httpx.post(f"{ENV.get('OLLAMA_BASE_URL')}/api/embed",
                       json={"model": ENV.get("EMBED_MODEL", "bge-m3"), "input": texts[i:i + 16]},
                       timeout=300)
        out.extend(r.json()["embeddings"])
    return np.asarray(out, dtype=np.float32)


def dashscope_embed(texts: list[str]) -> np.ndarray:
    key = os.environ.get("QWEN_API_KEY", "")
    out = []
    for i in range(0, len(texts), 10):
        r = httpx.post("https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
                       headers={"Content-Type": "application/json",
                                "Authorization": f"Bearer {key}"},
                       json={"model": "text-embedding-v3", "input": texts[i:i + 10],
                             "dimensions": 1024}, timeout=180)
        items = sorted(r.json()["data"], key=lambda x: x["index"])
        out.extend([it["embedding"] for it in items])
    return np.asarray(out, dtype=np.float32)


def norm(M: np.ndarray) -> np.ndarray:
    return M / np.clip(np.linalg.norm(M, axis=1, keepdims=True), 1e-9, None)


# ------------------------------------------------------------ 测试数据

QUERIES = [
    "美妆类目 10 到 50 万粉的腰部达人报价大概什么价位",
    "头部达人的档期一般要提前多久锁定",
    "商单立项的预算怎么分配，达人费用占比多少",
    "我们做过哪些美妆精华类的成功案例",
    "小红书美妆投放的注意事项和合规红线",
    "在途商单卡住了一般怎么跟进",
]

# 每条查询对应的正确文档（用语义不同的样本文本）
DOCS = [
    ("美妆类目达人刊例表 达人ID K0025 美妆日常 小红书 腰部 442.8w 精华护肤 32800 59700 135200", 0),
    ("美妆类目达人刊例表 K0009 美妆君 抖音 腰部 447.9w 精华护肤 32100 58300 128400", 0),
    ("抖音星图下单与结算规则 订单创建后达人须在48小时内确认接单 逾期自动取消", 1),
    ("巨量星图达人合作常见问题 档期冲突 头部达人档期通常需提前20-30天锁定", 1),
    ("商单立项与执行SOP 预算分配建议 达人费用占75%-85% 内容制作5%-10% 机动预算5%-15%", 2),
    ("商单立项标准 预算大于等于10万元或潜在年框客户 方可立项", 2),
    ("商单案例 DC20260081 沐光护肤 美妆精华护肤 结案ROI 1.24 达到立项基准", 3),
    ("商单案例 DC20260032 采薇彩妆 美妆精华护肤 结案ROI 2.46", 3),
    ("美妆类目投放方法论 内容要点 合规红线 不得使用美白祛斑等功效宣称", 4),
    ("美妆类目投放方法论 达人组合策略 1头部+3-5腰部+5-10尾部漏斗式组合", 4),
    ("在途商单跟踪单 DC20260036 采薇彩妆 美妆 当前阶段提案 最后更新", 5),
    ("在途商单风险规则 停滞大于等于7天或超期未结案为高风险 停滞大于等于3天为中风险", 5),
    # 干扰项
    ("母婴类目达人刊例表 奶粉辅食 纸尿裤 育儿知识达人", -1),
    ("汽车类目投放方法论 新能源整车试驾评测 车险服务", -1),
    ("服饰类目达人刊例表 女装 穿搭教程 OOTD", -1),
    ("餐饮探店类内容制作规范 美食制作 零食试吃", -1),
]

TEXTS = [d[0] for d in DOCS]
LABELS = [d[1] for d in DOCS]


def main() -> None:
    print("=" * 88)
    print("Embedding 质量对比：Ollama bge-m3（本地） vs DashScope text-embedding-v3（云端）")
    print("=" * 88)

    print("\n[1] 获取向量")
    try:
        V_local = ollama_embed(TEXTS)
        print(f"    Ollama bge-m3      : shape={V_local.shape}")
    except Exception as e:
        print(f"    ❌ Ollama 失败: {e}")
        return
    try:
        V_cloud = dashscope_embed(TEXTS)
        print(f"    DashScope v3       : shape={V_cloud.shape}")
    except Exception as e:
        print(f"    ⚠️ DashScope 不可用（{str(e)[:80]}），仅评估本地")
        V_cloud = None

    print("\n[2] 数值分布（检测量化痕迹）")
    for name, V in [("Ollama bge-m3", V_local), ("DashScope v3", V_cloud)]:
        if V is None:
            continue
        nz = np.abs(V[np.abs(V) > 0]).size
        tiny = np.abs(V[np.abs(V) < 1e-3]).size
        print(f"    {name:16s} 均值={V.mean():+.5f} 标准差={V.std():.5f} "
              f"|值|<1e-3 占比={tiny/V.size*100:.1f}% 不同取值数≈{len(np.unique(np.round(V,5)))}")

    print("\n[3] 语义区分度（正例相似度 vs 干扰项相似度）")
    results = {}
    for name, V in [("Ollama bge-m3", V_local), ("DashScope v3", V_cloud)]:
        if V is None:
            continue
        Vn = norm(V)
        Qn = norm(ollama_embed(QUERIES) if "Ollama" in name else dashscope_embed(QUERIES))
        S = Qn @ Vn.T
        pos, neg = [], []
        for qi, q in enumerate(QUERIES):
            for di, lab in enumerate(LABELS):
                if lab == qi:
                    pos.append(S[qi, di])
                elif lab == -1:
                    neg.append(S[qi, di])
        pos, neg = np.array(pos), np.array(neg)
        gap = pos.mean() - neg.mean()
        results[name] = gap
        print(f"    {name:16s} 正例均值={pos.mean():.4f} 干扰均值={neg.mean():.4f} "
              f"间隔={gap:+.4f} 正例最小={pos.min():.4f} 干扰最大={neg.max():.4f}")

    print("\n[4] 实际检索效果（Recall@5 / MRR，含干扰项）")
    for name, V in [("Ollama bge-m3", V_local), ("DashScope v3", V_cloud)]:
        if V is None:
            continue
        Vn = norm(V)
        Qn = norm(ollama_embed(QUERIES) if "Ollama" in name else dashscope_embed(QUERIES))
        S = Qn @ Vn.T
        hit5 = 0
        mrr = 0.0
        for qi in range(len(QUERIES)):
            order = np.argsort(-S[qi])
            rank = None
            for r, di in enumerate(order, 1):
                if LABELS[di] == qi:
                    rank = r
                    break
            if rank and rank <= 5:
                hit5 += 1
            if rank:
                mrr += 1.0 / rank
        n = len(QUERIES)
        print(f"    {name:16s} Recall@5={hit5/n:.3f}  MRR={mrr/n:.3f}")

    print("\n[5] 两模型向量空间是否可比（同一段文本的余弦相似度）")
    if V_cloud is not None:
        a, b = norm(V_local), norm(V_cloud)
        cross = float((a * b).sum(axis=1).mean())
        print(f"    同文本跨模型平均余弦相似度 = {cross:.4f}")
        print(f"    → {'⚠️ 两者向量空间不兼容，绝不能混用' if cross < 0.9 else '相似度较高'}")

    print("\n" + "=" * 88)
    print("结论：")
    if V_cloud is not None and results:
        best = max(results, key=results.get)
        print(f"  语义区分度最优: {best}（间隔 {results[best]:+.4f}）")
    print(f"  本地 bge-m3 向量可用性: 维度 {V_local.shape[1]}，{'通过' if V_local.shape[1]==1024 else '异常'}")
    print("=" * 88)


if __name__ == "__main__":
    main()
