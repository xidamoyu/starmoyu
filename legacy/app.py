"""Streamlit 前端：商单构思 / 刊例速查 / 在途跟进 / 人工确认。

启动：
    streamlit run app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from starmoyu.graph import StarMoyuGraph  # noqa: E402

st.set_page_config(page_title="MCN 商单资产智能助手", page_icon="📊", layout="wide")


@st.cache_resource
def get_graph() -> StarMoyuGraph:
    return StarMoyuGraph()


def new_thread(prefix: str) -> str:
    import time
    return f"{prefix}-{int(time.time() * 1000)}"


st.title("📊 MCN 商单资产智能助手")
st.caption("基于 LangGraph + RAG ｜ 私有商单资产沉淀与复用 ｜ 全程引用溯源")

# ---------------------------------------------------------------- 侧边栏：真实基础设施状态
with st.sidebar:
    st.header("⚙️ 系统状态")
    g = get_graph()
    st.caption(f"**编排** LangGraph ｜ Checkpointer: `{g.cp_kind}`")
    if not g.persistent:
        st.warning("当前为内存态 Checkpointer，进程重启会丢人工介入断点。\n"
                   "设置环境变量 `LANGGRAPH_CHECKPOINT_SQLITE=<path>` 可持久化。")
    try:
        from starmoyu import storage as _sto
        h = _sto.health()
        mil = h.get("milvus") or {}
        pg = h.get("postgres") or {}
        mn = h.get("minio") or {}
        st.markdown(
            f"- Milvus `{'ok' if mil.get('ok') else 'down'}`"
            f"（集合 {', '.join(mil.get('collections') or []) or '—'}）\n"
            f"- PostgreSQL `{'ok' if pg.get('ok') else 'down'}`"
            f"（{len(pg.get('tables') or [])} 张表）\n"
            f"- MinIO `{'ok' if mn.get('ok') else 'down'}`"
            f"（桶 {mn.get('bucket', '—')}）"
        )
    except Exception as e:
        st.error(f"基础设施自检失败：{type(e).__name__}: {e}")
    st.caption("**模型链路** DeepSeek(ARK) ／ bge-m3(Ollama) ／ gte-rerank-v2(DashScope)")

tab1, tab2, tab3 = st.tabs(["🎯 商单构思", "🔍 刊例/案例速查", "📋 在途商单跟进"])

# ---------------------------------------------------------------- 商单构思
with tab1:
    st.subheader("输入广告主需求，生成商单构思方案")
    st.caption("流程：需求解析 → 检索相似历史商单 → 达人匹配 → 生成方案 → **人工确认** → 定稿")
    req = st.text_area(
        "广告主需求",
        value="XX国货美妆品牌想推新款精华，预算15万，希望找4位腰部达人做种草，受众以女性为主，抖音和小红书都投",
        height=100,
    )
    if st.button("生成构思方案", type="primary", key="btn_proposal"):
        dg = get_graph()
        tid = new_thread("proposal")
        st.session_state["tid"] = tid
        with st.spinner("Agent 正在编排执行…"):
            out = dg.run(req, thread_id=tid)
        st.session_state["out"] = out

    out = st.session_state.get("out")
    if out and st.session_state.get("tid", "").startswith("proposal"):
        with st.expander("🔧 Agent 执行轨迹", expanded=True):
            for line in out.get("log", []):
                st.code(line, language=None)
            c1, c2, c3 = st.columns(3)
            c1.metric("命中历史商单", len(out.get("retrieved_cases") or []))
            c2.metric("候选达人", len(out.get("candidate_kols") or []))
            val = out.get("validation") or {}
            c3.metric("引用校验", "通过" if val.get("ok") else "需复核")

        if out.get("requirement"):
            with st.expander("📌 结构化需求"):
                st.json(out["requirement"])

        st.markdown("---")
        st.markdown(out.get("proposal") or "（未生成方案）")

        if out.get("candidate_kols"):
            with st.expander(f"👥 候选达人清单（{len(out['candidate_kols'])} 位）"):
                st.dataframe(
                    [{"达人ID": k["kol_id"], "昵称": k["kol_name"], "量级": k["tier"],
                      "粉丝量": f"{k['fans_count']/10000:.1f}w", "类目": f"{k['category']}/{k['sub_category']}",
                      "刊例(21-60s)": f"¥{k['price_21_60s']:,}", "互动率": f"{k['interact_rate']*100:.2f}%",
                      "评级": k["cooperation_level"]} for k in out["candidate_kols"]],
                    use_container_width=True)

        # ---- 人工介入面板 ----
        dg = get_graph()
        st.markdown("### 🧑‍💼 人工确认")
        st.caption("这是 Human-in-the-Loop 节点：图执行已在此处中断，等待你的决策。")
        feedback = st.text_input("修改意见（选择「提出修改」时生效）",
                                 placeholder="例如：达人减少到 2 位，总预算压到 9 万以内，优先选互动率高的")
        c1, c2 = st.columns(2)
        if c1.button("✅ 确认通过", use_container_width=True):
            with st.spinner("继续执行…"):
                res = dg.resume(st.session_state["tid"], action="approve")
            st.session_state["out"] = res
            st.success("已确认通过，流程结束。")
            st.rerun()
        if c2.button("✏️ 提出修改", use_container_width=True):
            with st.spinner("按意见重新生成…"):
                res = dg.resume(st.session_state["tid"], action="revise", feedback=feedback)
            st.session_state["out"] = res
            st.rerun()

# ---------------------------------------------------------------- 速查
with tab2:
    st.subheader("自然语言查询刊例、规则、历史案例")
    st.caption("走 LangGraph 的 `query` 分支（router → quick_query → finalize）")
    q = st.text_input("你的问题", value="商单立项的预算怎么分配，达人费用占比多少？")
    if st.button("查询", type="primary", key="btn_query"):
        dg = get_graph()
        with st.spinner("检索中…"):
            st_res = dg.run(q, thread_id=f"q-{abs(hash(q)) % 100000}")
        with st.expander("🧭 编排轨迹", expanded=False):
            for line in st_res.get("log", []):
                st.code(line, language=None)
        with st.expander("🔧 检索轨迹", expanded=False):
            st.json(st_res.get("trace"))
            st.json(st_res.get("validation"))
        st.markdown(st_res.get("answer") or "（无结果）")
        hits = st_res.get("raw_results") or []
        if hits:
            with st.expander(f"📄 命中片段（Top {len(hits)}）"):
                for i, c in enumerate(hits, 1):
                    st.markdown(f"**[{i}] {c.doc_title} · {c.section}** "
                                f"`{c.source_file}` rank(v={c.rank_vector}, b={c.rank_bm25})")
                    st.caption(c.content[:400].replace("\n", " "))

# ---------------------------------------------------------------- 跟进
with tab3:
    st.subheader("在途商单风险识别与跟进建议")
    st.caption("风险规则：停滞 ≥7 天或超期未结案 → 高风险；停滞 ≥3 天 → 中风险")
    if st.button("扫描在途商单", type="primary", key="btn_follow"):
        dg = get_graph()
        with st.spinner("分析中…"):
            st_res = dg.run("扫描所有在途商单的风险并给出跟进建议",
                            thread_id=f"f-{abs(hash('followup')) % 100000}")
        with st.expander("🧭 编排轨迹", expanded=False):
            for line in st_res.get("log", []):
                st.code(line, language=None)
        res = st_res.get("followup") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("在途总数", res.get("total", 0))
        c2.metric("高风险", (res.get("stats") or {}).get("高", 0))
        c3.metric("中风险", (res.get("stats") or {}).get("中", 0))
        c4.metric("正常", (res.get("stats") or {}).get("正常", 0))
        st.markdown("### 跟进建议")
        st.json(res.get("analysis", {}))
        with st.expander("📊 原始商单数据"):
            st.dataframe(
                [{"商单编号": d["deal_id"], "广告主": d["brand_name"], "阶段": d["stage"],
                  "负责人": d["owner"], "停滞天数": d["stagnant_days"], "风险": d["risk_level"],
                  "最后更新": str(d["updated_at"])[:10]} for d in (res.get("raw") or [])],
                use_container_width=True)
