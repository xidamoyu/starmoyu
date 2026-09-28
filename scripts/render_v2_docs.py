"""从当前 PG 真实数据渲染 RAG 语料：deal_case(结案) + rate_card(每类80-100达人) + inflight(在途)。
渲染结构与 v1 gen_data.py 一致，保证 chunk_meta/parent_chunk 可被 retriever 正常索引。
只写 data/raw/，不直接入库（入库由 ingest.py 负责）。
"""
import sys, os, random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from starmoyu import storage  # noqa: E402

RAW = ROOT / "data" / "raw"
random.seed(20260928)

RATE_CARD_PER_CAT = 100  # 每个类目取粉丝 top-N


def q(sql, args=None):
    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute(sql, args or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def j(x):
    return x if isinstance(x, (list, dict)) else (json.loads(x) if x else ({} if x == "{}" else []))


def fmt_money(v):
    return f"{float(v)/10000:.1f} 万" if float(v) >= 10000 else f"{float(v):,.0f} 元"


def render_rate_cards():
    cats = [r["category"] for r in q("SELECT DISTINCT category FROM kol_profile WHERE category IS NOT NULL")]
    docs = []
    for cat in sorted(cats):
        ks = q("""SELECT kol_id,kol_name,platform,category,sub_category,fans_count,tier,interact_rate,
                         price_21_60s,cpm,avg_views,cooperation_level
                  FROM kol_profile WHERE category=%s ORDER BY fans_count DESC NULLS LAST LIMIT %s""",
               (cat, RATE_CARD_PER_CAT))
        if len(ks) < 3:
            continue  # 小类目（<3 达人）不单独出表，避免碎片文档
        title = f"{cat}类目达人刊例表（2026 Q3）"
        L = [f"# {title}", "",
             f"适用类目：{cat}｜有效期：2026-07-01 至 2026-12-31｜币种：人民币", "",
             "| 达人ID | 达人昵称 | 平台 | 量级 | 粉丝量 | 主类目-细分 | 21-60秒刊例 | 互动率 | CPM | 均播 | 合作评级 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
        for k in ks:
            fans = f"{k['fans_count']/10000:.1f}w" if k['fans_count'] else "-"
            price = f"¥{k['price_21_60s']:,}" if k['price_21_60s'] else "-"
            ir = f"{k['interact_rate']*100:.2f}%" if k['interact_rate'] else "-"
            cpm = f"¥{k['cpm']:.0f}" if k.get('cpm') else "-"
            av = f"{k['avg_views']/10000:.1f}w" if k.get('avg_views') else "-"
            coop = {"true": "已挂靠", "false": "未挂靠"}.get(str(k.get('cooperation_level') or "").lower(), "-")
            L.append(f"| {k['kol_id']} | @{k['kol_name']} | {k['platform'] or '-'} | {k['tier'] or '-'} | "
                     f"{fans} | {k['sub_category'] or '-'} | {price} | {ir} | {cpm} | {av} | {coop} |")
        L += ["", "> 说明：以上为标准刊例价。定制内容上浮 20%-50%；打包 3 位以上达人可享 10%-20% 折扣；复购合作可谈至刊例 70%-85%。"]
        docs.append((f"rate_cards/{cat}类目达人刊例表.md", title, "\n".join(L)))
    return docs


def render_deal_cases():
    deals = q("""SELECT d.*, b.industry FROM deal d LEFT JOIN brand b ON d.brand_id=b.brand_id""")
    kmap = {k["kol_id"]: k for k in q("SELECT * FROM kol_profile")}
    docs = []
    for d in deals:
        kid_ids = j(d.get("kol_ids")) or []
        ks = [kmap[i] for i in kid_ids if i in kmap]
        if d["stage"] == "结案" and d.get("result_metrics"):
            m = j(d["result_metrics"])
            title = f"商单案例 {d['deal_id']}｜{d['brand_name']} {d['category']}-{d['sub_category'] or ''}"
            L = [f"# {title}", "",
                 f"- 商单编号：{d['deal_id']}",
                 f"- 广告主：{d['brand_name']}（{d.get('industry') or ''}）",
                 f"- 类目：{d['category']} / {d['sub_category'] or ''}",
                 f"- 投放目标：{d.get('goal') or ''}",
                 f"- 预算：{fmt_money(d['budget'])}",
                 f"- 执行周期：{d['start_date']} ~ {d['end_date']}",
                 f"- 主运营：{d['owner']}",
                 "", "## 一、客户需求", "", d.get("demand_desc") or "", "",
                 "## 二、达人组合与执行方案", "",
                 "| 达人ID | 昵称 | 量级 | 粉丝量 | 类目 | 刊例(21-60s) | CPM | 均播 |",
                 "|---|---|---|---|---|---|---|---|"]
            for k in ks:
                fans = f"{k['fans_count']/10000:.1f}w" if k['fans_count'] else "-"
                price = f"¥{k['price_21_60s']:,}" if k['price_21_60s'] else "-"
                cpm = f"¥{k['cpm']:.0f}" if k.get('cpm') else "-"
                av = f"{k['avg_views']/10000:.1f}w" if k.get('avg_views') else "-"
                L.append(f"| {k['kol_id']} | @{k['kol_name']} | {k['tier'] or '-'} | {fans} | "
                         f"{k['category'] or '-'}/{k['sub_category'] or '-'} | {price} | {cpm} | {av} |")
            exp = m.get('exposure', 0) or 0
            itr = m.get('interaction', 0) or 0
            roi = m.get('roi', 0) or 0
            L += ["", f"- 组合逻辑：以腰部达人为主，兼顾曝光与转化",
                  f"- 执行节奏：按 30 天标准周期推进。",
                  f"- 预算分配：达人费用 {fmt_money(float(d['budget'])*0.82)}，内容制作 {fmt_money(float(d['budget'])*0.08)}，机动 {fmt_money(float(d['budget'])*0.10)}。",
                  "", "## 三、结案数据", "",
                  f"- 总曝光量：{exp:,.0f}",
                  f"- 总互动量：{itr:,.0f}",
                  f"- 互动率：{itr/max(exp,1)*100:.2f}%",
                  f"- GMV：{m.get('gmv',0):,.0f} 元",
                  f"- ROI：{roi}",
                  f"- CPM：{m.get('cpm',0)} 元",
                  "", "## 四、复盘结论", "",
                  f"- 本单 ROI 为 {roi}，{'达到' if roi >= 1.0 else '未达到'}立项基准（1.0）。"]
            docs.append((f"deal_cases/{d['deal_id']}_案例.md", title, "\n".join(L)))
        else:
            title = f"在途商单跟踪单 {d['deal_id']}｜{d['brand_name']} {d['category']}"
            L = [f"# {title}", "",
                 f"- 商单编号：{d['deal_id']}",
                 f"- 广告主：{d['brand_name']}",
                 f"- 类目：{d['category']} / {d['sub_category'] or ''}",
                 f"- 当前阶段：{d['stage']}",
                 f"- 投放目标：{d.get('goal') or ''}",
                 f"- 预算：{fmt_money(d['budget'])}",
                 f"- 负责人：{d['owner']}",
                 f"- 执行周期：{d['start_date']} ~ {d['end_date']}",
                 "", "## 需求描述", "", d.get("demand_desc") or "", "",
                 "## 候选达人", ""]
            for k in ks:
                fans = f"{k['fans_count']/10000:.1f}w" if k['fans_count'] else "-"
                L.append(f"- {k['kol_id']} @{k['kol_name']}（{k['tier'] or '-'}，{fans} 粉，{k['category'] or '-'}/{k['sub_category'] or '-'}，报价 ¥{k['price_21_60s'] or 0:,}）")
            docs.append((f"deal_cases/inflight/{d['deal_id']}_跟踪单.md", title, "\n".join(L)))
    return docs


def main():
    import json
    docs = render_rate_cards() + render_deal_cases()
    n_case = sum(1 for p, _, _ in docs if p.startswith("deal_cases/") and "_案例" in p)
    n_rate = sum(1 for p, _, _ in docs if p.startswith("rate_cards/"))
    n_infl = sum(1 for p, _, _ in docs if "inflight/" in p)
    written = 0
    for rel, title, content in docs:
        p = RAW / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        written += 1
    print(f"渲染完成: 案例{n_case} 刊例{n_rate} 在途{n_infl} 总文件{written}")


if __name__ == "__main__":
    main()
