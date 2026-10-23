# -*- coding: utf-8 -*-
"""题库工厂：把「可出题素材」程序化枚举成题目，真值全部由语料正文计算。

为什么要有这个文件（而不是继续手写题库）：
  手写题库到 100 题就崩了——审计发现三类硬伤都是手写的必然产物：
    1. 同模板刷量（同句式罗列 27 个类目）；
    2. 指代不唯一（同品牌 4-12 篇文档，只写品牌名无法定位）；
    3. 真值篇数超窗口（真值 > 10 篇时 Recall@10 结构上限 < 1，指标失真）。
  程序化枚举的好处是每条组合的真值篇数在生成时就能算出来，不合约束直接丢弃，
  不会流进题库；同时可以按配额精确控制分层与难度分布。

核心约束（生成时强制，不满足即丢弃该组合）：
  - 单真值题（L1/L2）：真值必须恰好 1 篇；
  - 聚合题（L3）：真值必须落在 [2, 10] 篇；
  - 同一模板句式最多 STYLE 次，避免模板刷量；
  - 题干不得包含目标文档的品牌名/类目名（L2/L3），否则退化为字面匹配。

用法：被 build_eval_set_v6.py 导入，不单独运行。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

INFLIGHT_STAGES = ("需求沟通", "提案", "签约", "执行")


# ---------------- 文本工具 ----------------
def norm_cat(c: str | None) -> str:
    return (c or "").rstrip("-").strip()


def _q(texts: list[str], pat: str, group: int = 1) -> str | None:
    for t in texts:
        m = re.search(pat, t)
        if m:
            return m.group(group).strip()
    return None


# ---------------- 语料索引 ----------------
def load_deal_docs() -> list[dict]:
    """400 篇商单文档的结构化字段（全部来自正文正则解析，不读 deal 表）。"""
    from starmoyu import storage

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("select source_file, chunk_id, content from chunk_meta "
                    "where source_file like 'deal_cases/%' order by source_file, chunk_id")
        rows = cur.fetchall()

    grouped: dict[str, list[str]] = defaultdict(list)
    for f, _cid, content in rows:
        grouped[f].append(content)

    out = []
    for f, chunks in grouped.items():
        text = "\n".join(chunks)
        head = next((c for c in chunks if "广告主" in c), chunks[0])
        m = re.search(r"｜([^｜]+?)\s+([^\s｜]+)】", head)
        brand = m.group(1).strip() if m else None
        category = norm_cat(m.group(2)) if m else None
        if not category:
            cat_m = re.search(r"类目：([^\s/]+)", text)
            category = norm_cat(cat_m.group(1)) if cat_m else None
        platform = None
        for p in ("哔哩哔哩", "小红书", "抖音", "快手", "视频号"):
            if re.search(p + r"投放", text):
                platform = p
                break
        stage = _q(chunks, r"当前阶段：(\S+)")
        if not stage:
            stage = "结案" if "_案例" in f else None
        budget = _q(text, None) if False else None  # 占位，保持结构清晰
        bm = re.search(r"预算：([\d.]+)\s*万", text)
        roi_m = re.search(r"ROI[^\d]{0,6}([\d.]+)", text, re.I)
        expo_m = re.search(r"曝光[^\d]{0,6}([\d,]+)", text)
        qty_m = re.search(r"达人数量：(\d+)", text)
        out.append({
            "file": f,
            "kind": "case" if "_案例" in f else "inflight",
            "brand": brand,
            "category": category,
            "stage": stage,
            "goal": _q(chunks, r"投放目标：(\S+)"),
            "platform": platform,
            "budget_wan": float(bm.group(1)) if bm else None,
            "roi": float(roi_m.group(1)) if roi_m else None,
            "exposure": int(expo_m.group(1).replace(",", "")) if expo_m else None,
            "kol_qty": int(qty_m.group(1)) if qty_m else None,
            "chunks": len(chunks),
        })
    return out


def load_rate_cards() -> list[dict]:
    """27 张刊例表里每一位达人的一行数据。"""
    from starmoyu import storage

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("select source_file, chunk_id, content from chunk_meta "
                    "where source_file like 'rate_cards/%' order by source_file, chunk_id")
        rows = cur.fetchall()

    grouped: dict[str, list[str]] = defaultdict(list)
    for f, _cid, content in rows:
        grouped[f].append(content)

    people = []
    for f, chunks in grouped.items():
        head_m = re.search(r"【(.+?)类目达人刊例表", chunks[0])
        cat = norm_cat(head_m.group(1)) if head_m else None
        for t in chunks:
            for line in t.split("\n"):
                line = line.strip()
                if not line.startswith("|"):
                    continue
                cells = [x.strip() for x in line.strip("|").split("|")]
                if len(cells) < 11 or not re.fullmatch(r"K\d+", cells[0]):
                    continue
                fans = re.sub(r"[^\d.]", "", cells[4])
                price = re.sub(r"[^\d.]", "", cells[6])
                people.append({
                    "file": f, "cat": cat, "id": cells[0], "nick": cells[1],
                    "platform": cells[2], "tier": cells[3],
                    "fans_w": float(fans) if fans else None,
                    "price": int(float(price)) if price else None,
                    "rate": cells[7], "cpm": cells[8], "play": cells[9],
                    "rating": cells[10],
                })
    return people


def load_doc_chunks(prefix: str) -> dict[str, list[str]]:
    from starmoyu import storage

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("select source_file, chunk_id, content from chunk_meta "
                    "where source_file like %s order by source_file, chunk_id", (prefix + "%",))
        rows = cur.fetchall()
    grouped: dict[str, list[str]] = defaultdict(list)
    for f, _c, content in rows:
        grouped[f].append(content)
    return dict(grouped)


def load_deal_text() -> dict[str, str]:
    """商单文档全文（按文档拼接所有块），用于判断某达人在哪些商单里被提到。"""
    from starmoyu import storage

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("select source_file, chunk_id, content from chunk_meta "
                    "where source_file like 'deal_cases/%%' order by source_file, chunk_id")
        rows = cur.fetchall()
    grouped: dict[str, list[str]] = defaultdict(list)
    for f, _c, content in rows:
        grouped[f].append(content)
    return {f: "\n".join(v) for f, v in grouped.items()}


def inflight(d: dict) -> bool:
    """在途 = 未结案且未丢单。目录名不可信：inflight/ 里混了丢单文档。"""
    return d["stage"] in INFLIGHT_STAGES


# ---------------- 计数与唯一性 ----------------
def count(docs: list[dict], pred) -> int:
    return sum(1 for d in docs if pred(d))


def unique_files(docs: list[dict], pred) -> list[str]:
    return sorted(d["file"] for d in docs if pred(d))


def pick_one(docs: list[dict], pred, label: str) -> str | None:
    """单真值题：只有恰好命中 1 篇才算可用。"""
    files = unique_files(docs, pred)
    return files[0] if len(files) == 1 else None


def pick_range(docs: list[dict], pred, lo: int = 2, hi: int = 10) -> list[str] | None:
    """聚合题：真值篇数必须落在 [lo, hi]，否则丢弃（>hi 时 Recall@10 上限 < 1）。"""
    files = unique_files(docs, pred)
    return files if lo <= len(files) <= hi else None


if __name__ == "__main__":
    d = load_deal_docs()
    p = load_rate_cards()
    print(f"商单文档 {len(d)} 篇｜达人行 {len(p)} 条｜唯一昵称 {len({x['nick'] for x in p})} 个")
    print(f"品牌 {len({x['brand'] for x in d})}｜类目 {len({x['category'] for x in d})}")
