"""重构 eval_set.json 中 42 条 deal_case / rate_card / inflight 样本。
- 保留 12 条「规则/方法论」（policies/playbook 未变，原样）
- 按真实 deal/kol 数据反向生成新 query + label（源文件路径）
输出: data/eval/eval_set.json (覆盖)
"""
import sys, json, random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from starmoyu import storage  # noqa: E402

random.seed(20260928)
EVAL = ROOT / "data" / "eval" / "eval_set.json"


def q(sql, args=None):
    with storage.pg_connect() as c:
        cur = c.cursor(); cur.execute(sql, args or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def j(x):
    import json as _j
    return x if isinstance(x, (list, dict)) else (_j.loads(x) if x else [])


def main():
    old = json.loads(EVAL.read_text(encoding="utf-8"))
    keep = [x for x in old if x["type"] == "规则/方法论"]
    print(f"保留 规则/方法论 {len(keep)} 条")

    # ---- 案例详情（按结案单反向生成）----
    deals = q("""SELECT deal_id,brand_name,category,sub_category,budget,stage,result_metrics
                 FROM deal WHERE stage='结案' AND result_metrics IS NOT NULL ORDER BY deal_id""")
    cat_pick = {}
    for d in deals:
        cat_pick.setdefault(d["category"], []).append(d)

    case_detail = []
    # 每个类目挑 1-2 个，凑 ~16 条
    cats = sorted(cat_pick.keys())
    for ci, cat in enumerate(cats):
        for d in cat_pick[cat][:1 + (1 if ci % 3 == 0 else 0)]:
            m = j(d["result_metrics"])
            roi = m.get("roi", 0)
            qtext = f"{d['brand_name']}那个{d['sub_category'] or d['category']}的商单是什么预算，ROI 多少？"
            case_detail.append({
                "query": qtext,
                "labels": [f"deal_cases/{d['deal_id']}_案例.md"],
                "type": "案例详情", "difficulty": "hard" if roi < 1 else "medium",
            })
        if len(case_detail) >= 16:
            break

    # ---- 案例聚合（按类目）----
    case_agg = []
    for cat in ["美妆", "母婴", "食品饮料", "3C数码", "游戏", "服饰"]:
        docs = cat_pick.get(cat, [])
        if not docs:
            # 找相近类目
            for c2, ds in cat_pick.items():
                if cat in c2 or c2 in cat:
                    docs = ds; break
        if not docs:
            continue
        labels = [f"deal_cases/{d['deal_id']}_案例.md" for d in docs[:4]]
        case_agg.append({
            "query": f"我们做过哪些{cat}类目的成功案例，效果怎么样？",
            "labels": labels, "type": "案例聚合", "difficulty": "hard",
        })

    # ---- 刊例数值（按真实达人）----
    rate_q = []
    samples = q("""SELECT kol_name,category,sub_category,fans_count,tier,price_21_60s 
                   FROM kol_profile WHERE price_21_60s IS NOT NULL AND kol_name IS NOT NULL
                   ORDER BY fans_count DESC NULLS LAST LIMIT 200""")
    by_cat = {}
    for k in samples:
        by_cat.setdefault(k["category"], []).append(k)
    rc = 0
    for cat in sorted([c for c in by_cat.keys() if c]):
        if rc >= 5:
            break
        ks = by_cat[cat]
        k = random.choice(ks)
        rate_q.append({
            "query": f"{k['kol_name']}的21-60秒刊例报价是多少？",
            "labels": [f"rate_cards/{cat}类目达人刊例表.md"],
            "type": "刊例数值", "difficulty": "easy",
        })
        rc += 1

    # ---- 刊例数值+条件过滤 ----
    rate_filt = []
    for cat in sorted([c for c in by_cat.keys() if c]):
        if len(rate_filt) >= 5:
            break
        ks = by_cat[cat]
        waist = [k for k in ks if k["tier"] == "腰部"]
        if not waist:
            continue
        k = random.choice(waist)
        rate_filt.append({
            "query": f"有没有{cat}类的腰部达人，报价在{k['price_21_60s']}以内的？",
            "labels": [f"rate_cards/{cat}类目达人刊例表.md"],
            "type": "刊例数值+条件过滤", "difficulty": "medium",
        })

    # ---- 在途跟进 ----
    inflight = q("""SELECT deal_id,brand_name,category,stage FROM deal 
                    WHERE stage NOT IN ('结案','丢单') ORDER BY deal_id LIMIT 8""")
    infl_q = [{
        "query": f"{d['brand_name']}那个{d['category']}商单现在进展到哪一步了？",
        "labels": [f"deal_cases/inflight/{d['deal_id']}_跟踪单.md"],
        "type": "在途跟进", "difficulty": "medium",
    } for d in inflight]

    new = keep + case_detail[:16] + case_agg + rate_q + rate_filt + infl_q
    # 补齐到 54
    target = 54
    if len(new) < target:
        extra = [x for x in old if x["type"] in ("刊例数值", "刊例数值+条件过滤")][:target - len(new)]
        new += extra
    new = new[:target]

    EVAL.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    print("重构完成:", len(new), "条")
    print("类型分布:", dict(Counter(x["type"] for x in new)))


if __name__ == "__main__":
    main()
