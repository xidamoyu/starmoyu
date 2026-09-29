"""沉淀流服务：业务运转产生的新非结构化语料 → 确认 → 增量入 RAG。

四条沉淀通道（P1-P4）：
- P1 结案案例回流：结案确认后渲染案例文档（含跟进流水/复盘经验），增量嵌入入库
- P2 经验抽取：从跟进流水提取经验要点进案例「复盘结论」区
- P3 party_traits：合作画像沉淀（表/Service 既有，本服务提供 trait→语料的渲染）
- P4 跟进笔记富化：agent_tools.create_followup 侧引导（不改本文件）

增量入库原则：只 embed 新文档的块，绝不 TRUNCATE / 全量重跑（区别于 reindex_rag.py）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage, llm  # noqa: E402
from starmoyu.ingest import parse_markdown, split_section, classify_chunk, extract_meta  # noqa: E402


def _q(sql, args=None):
    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute(sql, args or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fmt_money(v):
    v = float(v or 0)
    return f"{v/10000:.1f} 万" if v >= 10000 else f"{v:,.0f} 元"


# ============================================================ 案例渲染（P1+P2）

def fetch_deal_full(deal_id: str) -> dict | None:
    """deal 主表 + 跟进流水 + traits + 达人明细，一次取齐。"""
    deals = _q("""SELECT d.*, b.industry FROM deal d
                  LEFT JOIN brand b ON d.brand_id=b.brand_id
                  WHERE d.deal_id=%s""", (deal_id,))
    if not deals:
        return None
    d = deals[0]
    d["followups"] = _q("""SELECT action_type, note, operator, created_at::text, stage_from, stage_to
                           FROM deal_followup WHERE deal_id=%s ORDER BY created_at""", (deal_id,))
    d["traits"] = _q("""SELECT party_id, trait_category, trait_content, severity
                        FROM party_traits WHERE deal_id=%s AND verified=TRUE""", (deal_id,))
    kid_ids = d.get("kol_ids")
    if isinstance(kid_ids, str):
        kid_ids = json.loads(kid_ids or "[]")
    d["kol_ids"] = list(kid_ids) if kid_ids else []
    if d["kol_ids"]:
        ph = ",".join(["%s"] * len(d["kol_ids"]))
        d["kols"] = _q(f"SELECT * FROM kol_profile WHERE kol_id IN ({ph})", tuple(d["kol_ids"]))
    else:
        d["kols"] = []
    if isinstance(d.get("result_metrics"), str):
        d["result_metrics"] = json.loads(d["result_metrics"] or "null")
    return d


def extract_lessons(followups: list[dict]) -> list[str]:
    """P2：从跟进流水提取经验要点（规则式，不用 LLM——确定可解释）。

    提取维度：拒绝/砍价/返点/档期/内容修改/复购意向。
    """
    lessons: list[str] = []
    seen = set()

    def add(s: str):
        if s and s not in seen:
            seen.add(s)
            lessons.append(s)

    for f in followups:
        note = (f.get("note") or "").strip()
        if not note or note.startswith("{"):
            continue  # 跳过结案 JSON 留痕
        at = f.get("action_type") or ""
        low = note.lower()
        if any(k in note for k in ("拒绝", "拒了", "婉拒", "没接")):
            add(f"合作被拒：{note}（{at}）")
        elif any(k in low for k in ("压价", "砍价", "降价", "还价")):
            add(f"价格博弈：{note}")
        if "返点" in low or "返20" in low or "反点" in low:
            add(f"返点信息：{note}")
        if any(k in note for k in ("档期", "排期", "延后", "推迟")):
            add(f"档期协调：{note}")
        if any(k in note for k in ("改稿", "修改", "二创", "脚本")):
            add(f"内容协作：{note}")
        if any(k in note for k in ("复购", "下次", "二期", "续投")):
            add(f"复购意向：{note}")
    return lessons[:6]


def render_case_md(deal_id: str, extra_lessons: list[str] | None = None) -> tuple[str, str, str] | None:
    """渲染结案案例文档（结构对齐 render_v2_docs.py，增强：流水复盘 + traits）。

    Returns: (rel_path, title, content) 或 None（商单不存在/未结案）。
    """
    d = fetch_deal_full(deal_id)
    if not d:
        return None
    if d["stage"] != "结案" or not d.get("result_metrics"):
        raise ValueError(f"商单 {deal_id} 未结案或无 result_metrics，不能渲染结案案例")

    m = d["result_metrics"] or {}
    exp, itr = m.get("exposure", 0) or 0, m.get("interaction", 0) or 0
    roi = m.get("roi", 0) or 0
    title = f"商单案例 {d['deal_id']}｜{d['brand_name']} {d['category']}-{d['sub_category'] or ''}"
    L = [f"# {title}", "",
         f"- 商单编号：{d['deal_id']}",
         f"- 广告主：{d['brand_name']}（{d.get('industry') or ''}）",
         f"- 类目：{d['category']} / {d['sub_category'] or ''}",
         f"- 投放目标：{d.get('goal') or ''}",
         f"- 预算：{_fmt_money(d['budget'])}",
         f"- 执行周期：{d['start_date']} ~ {d['end_date']}",
         f"- 主运营：{d['owner']}",
         "", "## 一、客户需求", "", d.get("demand_desc") or "", "",
         "## 二、达人组合与执行方案", "",
         "| 达人ID | 昵称 | 量级 | 粉丝量 | 类目 | 刊例(21-60s) | CPM | 均播 |",
         "|---|---|---|---|---|---|---|---|"]
    for k in d["kols"]:
        fans = f"{k['fans_count']/10000:.1f}w" if k['fans_count'] else "-"
        price = f"¥{k['price_21_60s']:,}" if k['price_21_60s'] else "-"
        cpm = f"¥{k['cpm']:.0f}" if k.get('cpm') else "-"
        av = f"{k['avg_views']/10000:.1f}w" if k.get('avg_views') else "-"
        L.append(f"| {k['kol_id']} | @{k['kol_name']} | {k['tier'] or '-'} | {fans} | "
                 f"{k['category'] or '-'}/{k['sub_category'] or '-'} | {price} | {cpm} | {av} |")
    L += ["", f"- 组合逻辑：以腰部达人为主，兼顾曝光与转化",
          f"- 执行节奏：按 30 天标准周期推进。",
          "", "## 三、结案数据", "",
          f"- 总曝光量：{exp:,.0f}",
          f"- 总互动量：{itr:,.0f}",
          f"- 互动率：{itr/max(exp,1)*100:.2f}%",
          f"- GMV：{m.get('gmv',0):,.0f} 元",
          f"- ROI：{roi}",
          f"- CPM：{m.get('cpm',0)} 元"]

    # P2：跟进流水提炼的经验 + 用户补充
    lessons = extract_lessons(d["followups"]) + [s for s in (extra_lessons or []) if s.strip()]
    L += ["", "## 四、复盘结论", "",
          f"- 本单 ROI 为 {roi}，{'达到' if roi >= 1.0 else '未达到'}立项基准（1.0）。"]
    if lessons:
        L += ["- 过程经验："]
        L += [f"  - {x}" for x in lessons]
    else:
        L += ["- 过程经验：跟进流水暂无可提炼要点。"]

    # P3：结案时确认的合作画像
    if d["traits"]:
        L += ["", "## 五、合作画像（已确认）", ""]
        for t in d["traits"]:
            L.append(f"- @{t['party_id']}【{t['trait_category']}】{t['trait_content']}")

    # 跟进大事记（有则附）
    key_fs = [f for f in d["followups"]
              if (f.get("note") or "") and not (f.get("note") or "").startswith("{")]
    if key_fs:
        L += ["", "## 六、跟进大事记", ""]
        for f in key_fs[-12:]:
            L.append(f"- {f['created_at'][:16]} [{f['action_type']}] {f['note']}（{f['operator']}）")

    rel = f"deal_cases/{d['deal_id']}_案例.md"
    return rel, title, "\n".join(L)


# ============================================================ 增量入库

def ingest_document_incremental(rel_path: str, title: str, content: str) -> dict:
    """单文档增量入库：切块 → embed → PG chunk 表 + Milvus（不动已有数据）。

    幂等：同 source_file 重复入库先删旧块（更新场景）。
    """
    RAW = ROOT / "data" / "raw"
    p = RAW / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

    text_stored, sections = parse_markdown(content)
    doc_id = rel_path.replace("/", "__").replace(".md", "")
    meta = extract_meta(rel_path, title, content)
    object_key = f"docs/{rel_path}"

    parents, chunks = [], []
    for si, (sec_title, sec_body) in enumerate(sections):
        parent_id = f"{doc_id}#s{si}"
        parents.append({"parent_id": parent_id, "doc_id": doc_id, "doc_title": title,
                        "section": sec_title, "content": f"{sec_title}\n{sec_body}"})
        for ci, ck in enumerate(split_section(sec_body)):
            content_c = f"【{title}】{sec_title}\n{ck}" if sec_title else f"【{title}】\n{ck}"
            chunks.append({
                "chunk_id": f"{parent_id}#c{ci}", "parent_id": parent_id, "doc_id": doc_id,
                "doc_title": title, "doc_type": meta["doc_type"], "source_file": rel_path,
                "object_key": object_key, "section": sec_title,
                "chunk_type": classify_chunk(sec_title, ck),
                "category": meta["category"] or "", "platform": meta["platform"] or "",
                "tier": meta["tier"] or "", "deal_year": meta["deal_year"] or 0,
                "char_len": len(content_c), "content": content_c,
            })
    if not chunks:
        return {"ok": False, "error": "切块结果为空"}

    # 1) PG：先删同 source_file 旧块（幂等），再插新块
    with storage.pg_connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT chunk_id FROM chunk_meta WHERE source_file=%s", (rel_path,))
            old_ids = [r[0] for r in cur.fetchall()]
            if old_ids:
                ph = ",".join(["%s"] * len(old_ids))
                cur.execute(f"DELETE FROM chunk_meta WHERE chunk_id IN ({ph})", tuple(old_ids))  # noqa: S608
                cur.execute("DELETE FROM parent_chunk WHERE doc_id=%s", (doc_id,))
            cur.executemany(
                "INSERT INTO parent_chunk VALUES (%s,%s,%s,%s,%s)",
                [(x["parent_id"], x["doc_id"], x["doc_title"], x["section"], x["content"])
                 for x in parents])
            cur.executemany(
                "INSERT INTO chunk_meta VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(c["chunk_id"], c["parent_id"], c["doc_id"], c["doc_title"], c["doc_type"],
                  c["source_file"], c["object_key"], c["section"], c["chunk_type"], c["category"],
                  c["platform"], c["tier"], c["deal_year"], c["char_len"], c["content"])
                 for c in chunks])
        con.commit()

    # 2) Milvus：删旧向量（按 chunk_id 主键）再插入
    client = storage.milvus_client()
    if old_ids:
        client.delete(storage.COLLECTION, ids=old_ids)
    vecs = llm.embed([c["content"] for c in chunks])
    rows = []
    for c, v in zip(chunks, vecs):
        r = dict(c); r["embedding"] = v; rows.append(r)
    client.insert(storage.COLLECTION, rows)
    client.flush(storage.COLLECTION)

    return {"ok": True, "doc_id": doc_id, "chunks": len(chunks), "parents": len(parents),
            "replaced_old": len(old_ids), "file": str(p)}


# ============================================================ 结案沉淀一条龙

def sediment_closed_deal(deal_id: str, extra_lessons: list[str] | None = None,
                         created_by: str | None = None) -> dict:
    """结案沉淀入口：渲染案例 →（此处不自动入库，返回草稿给确认卡）。

    分两步设计：render（本函数）出草稿 → 前端确认卡展示 → confirm_sediment 才真正入库。
    人工关口原则与 M4/M5 一致。
    """
    r = render_case_md(deal_id, extra_lessons)
    if r is None:
        raise ValueError(f"商单不存在: {deal_id}")
    rel, title, content = r
    preview = content[:600] + ("…" if len(content) > 600 else "")
    return {"deal_id": deal_id, "rel_path": rel, "title": title,
            "char_len": len(content), "preview": preview}


def confirm_sediment(deal_id: str, extra_lessons: list[str] | None = None,
                     confirmed_by: str | None = None) -> dict:
    """确认卡批准：真正渲染全文 + 增量入库。"""
    r = render_case_md(deal_id, extra_lessons)
    if r is None:
        raise ValueError(f"商单不存在: {deal_id}")
    rel, title, content = r
    ing = ingest_document_incremental(rel, title, content)
    return {"ok": True, "deal_id": deal_id, "sediment": ing,
            "confirmed_by": confirmed_by or "agent"}
