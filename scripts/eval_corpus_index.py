# -*- coding: utf-8 -*-
"""从语料正文派生商单结构化索引，供题库构建与真值复算使用。

不读 PostgreSQL 的 deal 表——检索命中的是文档，真值必须以文档正文为唯一依据。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

INFLIGHT_STAGES = ("需求沟通", "提案", "签约", "执行")


def parse_num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"[\d.]+", s.replace(",", ""))
    return float(m.group(0)) if m else None


def derive_deal_docs() -> list[dict]:
    """返回每份商单文档的结构化字段（正文解析）。"""
    from starmoyu import storage

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("select source_file, chunk_id, content from chunk_meta "
                    "where source_file like 'deal_cases/%' order by source_file, chunk_id")
        rows = cur.fetchall()

    grouped: dict[str, list[str]] = {}
    for f, _cid, content in rows:
        grouped.setdefault(f, []).append(content)

    out = []
    for f, chunks in grouped.items():
        text = "\n".join(chunks)
        head = next((c for c in chunks if "广告主" in c), chunks[0])
        m = re.search(r"｜([^｜]+?)\s+([^\s｜]+)】", head)
        brand = m.group(1).strip() if m else None
        category = (m.group(2).strip() if m else None)
        if not category:
            cat_m = re.search(r"类目：([^\s/]+)", text)
            category = cat_m.group(1) if cat_m else None
        stage_m = re.search(r"当前阶段：(\S+)", text)
        goal_m = re.search(r"投放目标：(\S+)", text)
        platform = None
        for p in ("哔哩哔哩", "小红书", "抖音", "快手", "视频号"):
            if re.search(p + r"投放", text):
                platform = p
                break
        budget_m = re.search(r"预算：([\d.]+)\s*万", text)
        cpm_req = re.search(r"CPM≤([\d.]+)", text)
        roi_m = re.search(r"ROI[^\d]{0,6}([\d.]+)", text, re.I)
        expo_m = re.search(r"曝光[^\d]{0,6}([\d,]+)", text)
        out.append({
            "file": f,
            "kind": "case" if "_案例" in f else "inflight",
            "in_inflight_dir": "inflight/" in f,
            "brand": brand,
            "category": category,
            "stage": stage_m.group(1) if stage_m else ("结案" if "_案例" in f else None),
            "goal": goal_m.group(1) if goal_m else None,
            "platform": platform,
            "budget_wan": float(budget_m.group(1)) if budget_m else None,
            "cpm_req": float(cpm_req.group(1)) if cpm_req else None,
            "roi": float(roi_m.group(1)) if roi_m else None,
            "exposure": int(expo_m.group(1).replace(",", "")) if expo_m else None,
            "chunks": len(chunks),
            "has_result": bool(roi_m or expo_m),
        })
    return out


def inflight(d: dict) -> bool:
    """「在途」= 未结案且未丢单。目录名不可信：inflight/ 里混了丢单文档。"""
    return d["stage"] in INFLIGHT_STAGES


if __name__ == "__main__":
    docs = derive_deal_docs()
    print(f"商单文档 {len(docs)} 份")
    from collections import Counter
    print("kind × stage:", Counter((d["kind"], d["stage"]) for d in docs))
    print("inflight 目录内容:", Counter(d["stage"] for d in docs if d["in_inflight_dir"]))
    print("平台分布:", Counter(d["platform"] for d in docs))
    print("目标分布:", Counter(d["goal"] for d in docs))
    print("有结果指标:", sum(1 for d in docs if d["has_result"]))
