# -*- coding: utf-8 -*-
"""标注金标准核验：以文档正文为准，重建每条多约束题的真值集合，与人工标注比对。

为什么不能拿 PostgreSQL 的 deal 表当唯一真值：
  文档正文里「需求描述」段才写投放平台（哔哩哔哩/抖音/小红书），
  deal.platform_req 是另一套字段，两者不完全一致；检索要命中的是文档。

用法：.venv/Scripts/python.exe scripts/verify_eval_labels.py
输出：reports/eval_label_verify.json / .md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVAL = ROOT / "reports" / "eval_set_v3.json"
OUT_JSON = ROOT / "reports" / "eval_label_verify.json"
OUT_MD = ROOT / "reports" / "eval_label_verify.md"

INFLIGHT_STAGES = {"需求沟通", "提案", "签约", "执行"}  # 「在途」= 尚未结案也未丢单


def parse_docs() -> dict[str, dict]:
    """从 chunk 正文解析每份商单文档的结构化字段。"""
    from starmoyu import storage

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("select source_file, chunk_id, content from chunk_meta "
                    "where source_file like 'deal_cases/%' order by source_file, chunk_id")
        rows = cur.fetchall()

    docs: dict[str, dict] = {}
    for f, _cid, content in rows:
        d = docs.setdefault(f, {"chunks": 0, "text": [], "kind": "inflight" if "inflight" in f else "case"})
        d["chunks"] += 1
        d["text"].append(content)
    out = {}
    for f, d in docs.items():
        text = "\n".join(d["text"])
        head = d["text"][0]
        m_brand = re.search(r"｜([^｜\s]+)", head)
        m_cat = re.search(r"类目：(\S+)", text)
        m_stage = re.search(r"当前阶段：(\S+)", text)
        m_goal = re.search(r"投放目标：(\S+)", text)
        platform = None
        for p in ("哔哩哔哩", "小红书", "抖音", "快手", "视频号"):
            if re.search(p + r"投放", text):
                platform = p
                break
        out[f] = {
            "kind": d["kind"],
            "chunks": d["chunks"],
            "brand": m_brand.group(1) if m_brand else None,
            "category": m_cat.group(1) if m_cat else None,
            "stage": m_stage.group(1) if m_stage else ("结案" if d["kind"] == "case" else None),
            "goal": m_goal.group(1) if m_goal else None,
            "platform": platform,
            "has_metric": bool(re.search(r"曝光|互动|CPM|ROI|GMV|转化", text)),
        }
    return out


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = {x["id"]: x for x in es["items"]}
    docs = parse_docs()

    def truth(pred) -> set[str]:
        return {f for f, d in docs.items() if pred(d)}

    CASES = [
        ("L3-001", "美妆类目结案案例", lambda d: d["kind"] == "case" and d["category"] == "美妆"),
        ("L3-002", "美食类目结案案例", lambda d: d["kind"] == "case" and d["category"] == "美食"),
        ("L3-003", "汽车类目结案案例", lambda d: d["kind"] == "case" and d["category"] == "汽车"),
        ("L3-004", "运动健身类目结案案例", lambda d: d["kind"] == "case" and d["category"] == "运动健身"),
        ("L3-005", "旅行类目结案案例", lambda d: d["kind"] == "case" and d["category"] == "旅行"),
        ("L3-006", "萌宠类目结案案例", lambda d: d["kind"] == "case" and d["category"] == "萌宠"),
        ("L3-007", "完美日记全部商单", lambda d: d["brand"] == "完美日记"),
        ("L3-008", "云记全部商单", lambda d: d["brand"] == "云记"),
        ("L3-009", "王者荣耀在途", lambda d: d["brand"] == "王者荣耀" and d["stage"] in INFLIGHT_STAGES),
        ("L2-022", "王者荣耀在途（与 L3-009 同真值）", lambda d: d["brand"] == "王者荣耀" and d["stage"] in INFLIGHT_STAGES),
        ("L2-023", "王者荣耀在途（与 L3-009 同真值）", lambda d: d["brand"] == "王者荣耀" and d["stage"] in INFLIGHT_STAGES),
        ("L3-010", "美妆在途", lambda d: d["category"] == "美妆" and d["stage"] in INFLIGHT_STAGES),
        ("L3-011", "母婴在途", lambda d: d["category"] == "母婴" and d["stage"] in INFLIGHT_STAGES),
        ("L3-013", "母婴在途（参考单）", lambda d: d["category"] == "母婴" and d["stage"] in INFLIGHT_STAGES),
        ("L3-016", "在途+新品推广+哔哩哔哩", lambda d: d["stage"] in INFLIGHT_STAGES and d["goal"] == "新品推广" and d["platform"] == "哔哩哔哩"),
        ("L3-017", "在途+品牌曝光+哔哩哔哩", lambda d: d["stage"] in INFLIGHT_STAGES and d["goal"] == "品牌曝光" and d["platform"] == "哔哩哔哩"),
        ("L3-018", "美妆结案ROI达标", lambda d: d["kind"] == "case" and d["category"] == "美妆" and d["has_metric"]),
    ]

    results = []
    for iid, name, pred in CASES:
        if iid not in items:
            continue
        lab = items[iid]["labels"]
        t = truth(pred)
        miss = sorted(t - set(lab))
        extra = sorted(set(lab) - t)
        results.append({
            "id": iid, "name": name, "query": items[iid]["query"],
            "truth_n": len(t), "label_n": len(lab),
            "missing": miss, "extra": extra,
            "recall10_cap": round(min(10, len(t)) / len(t), 3) if t else None,
            "consistent": not miss and not extra,
        })

    # 单文档题的标注是否存在（另查）
    single = []
    for iid, it in items.items():
        if it["difficulty"] == "L4" or len(it["labels"]) > 2:
            continue
        for f in it["labels"]:
            if f not in docs and not f.startswith(("rate_cards", "policies", "playbook", "imports")):
                single.append({"id": iid, "missing_file": f})

    OUT_JSON.write_text(json.dumps({"cases": results, "single_doc_missing": single},
                                   ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# 标注金标准核验报告", "",
         "- 核验对象：`reports/eval_set_v3.json` 中的多约束聚合题（L3）与跨题共用真值的题",
         "- 真值来源：**文档正文**（类目 / 当前阶段 / 投放目标 / 需求描述里的平台），不依赖 PostgreSQL 字段，",
         "  因为文档里的「需求描述」段才写投放平台，deal 表字段与文档不完全一致，而检索命中的是文档",
         "- 判定：真值集合与人工标注集合完全相等即「一致」", "",
         "## 一、逐题核验", "",
         "| 题号 | 题面 | 真值篇数 | 标注篇数 | 漏标 | 多标 | 结论 |",
         "|---|---|---|---|---|---|---|"]
    bad = 0
    for r in results:
        verdict = "一致" if r["consistent"] else "**不一致**"
        if not r["consistent"]:
            bad += 1
        L.append(f"| {r['id']} | {r['name']} | {r['truth_n']} | {r['label_n']} | "
                 f"{len(r['missing'])} | {len(r['extra'])} | {verdict} |")
    L += ["", f"合计：{len(results)} 道题受检，其中 **{bad} 道标注与正文真值不一致**。", "",
          "## 二、不一致明细", ""]
    for r in results:
        if r["consistent"]:
            continue
        L.append(f"### {r['id']}　{r['query']}")
        L.append(f"- 真值 {r['truth_n']} 篇 / 标注 {r['label_n']} 篇")
        if r["recall10_cap"] is not None:
            L.append(f"- Recall@10 结构上限：**{r['recall10_cap']}**（真值 {r['truth_n']} 篇 > 取回窗口 10 时，"
                     f"满分也不可能拿到 1.0）")
        if r["missing"]:
            L.append(f"- 漏标 {len(r['missing'])} 篇：{', '.join('`' + x + '`' for x in r['missing'][:12])}")
        if r["extra"]:
            L.append(f"- 多标 {len(r['extra'])} 篇（正文条件不符）：{', '.join('`' + x + '`' for x in r['extra'][:12])}")
        L.append("")
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"checked={len(results)} inconsistent={bad}")
    for r in results:
        if not r["consistent"]:
            print(f"  {r['id']}: truth={r['truth_n']} label={r['label_n']} "
                  f"miss={len(r['missing'])} extra={len(r['extra'])} cap={r['recall10_cap']}")


if __name__ == "__main__":
    main()
