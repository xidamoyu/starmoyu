# -*- coding: utf-8 -*-
"""测评集质量审计：确定性规则，不调用大模型。

判据演进（每条都对应一次真实的误判，改之前先读）：
  1. 「疑似漏标」只认长度 >= 3 的判别词。2 字滑窗（「合理」「价合」）在几百份文档里
     天然普现，用它判漏标会把每道短句题都标成漏标。
  2. 模板簇判定必须先把实体名和问法参数抹成占位符再比。题库是有意做成
     「同一问法 × 多个实体」的覆盖设计，按字面比会把有效覆盖算成刷量。
  3. 抹槽位的顺序是「先参数短语、后实体名」。「口碑测评」含类目名「测评」，
     顺序反了会把参数短语切成两半，同一问法的骨架反而判不相等。
  4. 措辞雷同只报配对、不做并查集簇。单链接聚类会把 A~B 相似、B~C 相似串成
     一个大团，而 A 与 C 毫无关系，把有效覆盖算成冗余。
  5. 短骨架（槽位密度 >= 45%）不算措辞雷同：固定部分只剩十几个字时，
     两条不同骨架的字符相似度天然到 0.7 以上，是算术结果不是缺陷。
  6. 分层配额优先读题库自带的 generation.quota。写死设计比例会把
     「按 70/100/80/50 有意生成」的题库判成全部缺口。
"""
"""测评集质量审计 v2：去掉跨文档共性词噪声，量化判别力与标注缺陷。

产出：
  reports/eval_set_audit.json   机读明细
  reports/eval_set_audit.md     人读报告

判定全部确定性（不调用大模型），核心思路：
  1. 关键词命中要按「文档频次」过滤——刊例表/案例模板共享大量固定串，
     未过滤时任何题都会"漏标"，属假阳性。
  2. 判别力量化：题干是否字面包含目标文档的类目/品牌名 → 若包含，
     该题只需"名字匹配"，不检验语义检索能力。
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVAL = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "reports" / "eval_set_v3.json"
OUT_JSON = ROOT / "reports" / f"eval_set_audit_{EVAL.stem.replace('eval_set_', '')}.json"
OUT_MD = ROOT / "reports" / f"eval_set_audit_{EVAL.stem.replace('eval_set_', '')}.md"

DESIGN_QUOTA = {"L1": 0.25, "L2": 0.30, "L3": 0.30, "L4": 0.12}


def toks(text: str) -> list[str]:
    """中文 2-3 字滑窗 + 英数整词，过滤停用词。"""
    out = re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z0-9]+", text)
    grams: list[str] = []
    for t in out:
        if re.fullmatch(r"[\u4e00-\u9fa5]+", t):
            if len(t) <= 3:
                grams.append(t)
            else:
                grams += [t[i:i + 2] for i in range(len(t) - 1)]
                grams += [t[i:i + 3] for i in range(len(t) - 2)]
        else:
            grams.append(t)
    return grams


def tri(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text)
    return {t[i:i + 3] for i in range(len(t) - 2)} or {t}


def jac(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def main() -> None:
    from starmoyu import storage  # noqa: WPS433

    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = es["items"]

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("select source_file, count(*) from chunk_meta group by 1")
        chunks_per_doc = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("select source_file, chunk_id, content from chunk_meta")
        rows = cur.fetchall()

    n_docs = len(chunks_per_doc)
    # 文档频次：每个 n-gram 出现在多少份文档里（同文档多次只计 1）
    df: Counter = Counter()
    text_by_doc: dict[str, list[str]] = defaultdict(list)
    for f, _cid, content in rows:
        text_by_doc[f].append(content)
    for f, texts in text_by_doc.items():
        seen = set()
        for t in texts:
            seen.update(toks(t))
        for g in seen:
            df[g] += 1
    # 出现在 >8% 文档里的词视为语料模板串（刊例表表头、案例字段名等）
    BOILER = {g for g, c in df.items() if c > max(3, n_docs * 0.08)}
    DOC_TEXT = {f: "\n".join(ts) for f, ts in text_by_doc.items()}

    # direct 标注共享度
    doc_shared = Counter()
    for it in items:
        for f, rel in it["labels"].items():
            doc_shared[f] += 1

    audit: dict = {"eval_set_version": es["eval_set_version"], "n_items": len(items),
                   "corpus_docs": n_docs, "corpus_chunks": sum(chunks_per_doc.values()),
                   "boilerplate_terms": len(BOILER), "per_item": []}

    for it in items:
        rec: dict = {"id": it["id"], "layer": it["difficulty"], "query": it["query"],
                     "labels": it["labels"], "problems": [], "flags": {}}
        probs: list[str] = rec["problems"]
        labels = it["labels"]
        n_lab = len(labels)
        n_direct = sum(1 for v in labels.values() if v == "direct")

        # ---- A 标注完整性 ----
        missing = [f for f in labels if f not in chunks_per_doc]
        if missing:
            probs.append(f"标注文件已不存在于语料: {missing}")
        if it["difficulty"] != "L4":
            if n_lab == 0:
                probs.append("无任何标注")
            elif n_direct == 0:
                probs.append("无 direct 标注 → Hit@1/Hit@5 结构上恒为 0，白占分母")
        if it["difficulty"] == "L4" and labels:
            probs.append("拒答题却带标注")

        # ---- B 标注粒度（块数越多，文档级命中越廉价）----
        cs = [chunks_per_doc.get(f, 0) for f in labels]
        rec["flags"]["label_chunk_counts"] = cs
        rec["flags"]["label_chunks_max"] = max(cs) if cs else 0
        if cs and max(cs) >= 30 and it["difficulty"] in ("L1", "L2"):
            probs.append(f"标注文档过大（{max(cs)} 块），任一块命中即算命中该篇")
        # 该题标注文档的总块数占语料比例 → 随机命中概率
        rec["flags"]["label_chunk_share"] = round(sum(cs) / max(1, sum(chunks_per_doc.values())), 4)
        if rec["flags"]["label_chunk_share"] >= 0.05 and it["difficulty"] in ("L1", "L2"):
            probs.append(f"标注文档块数占语料 {rec['flags']['label_chunk_share']:.1%}，随机命中概率偏高")

        # ---- C 标注唯一性（只对单真值题：L1/L2 一道题只指一篇商单，被多题共享说明指代不清。
        #       L3 聚合题共享正常：同一份商单本来就属于多个类目/目标/平台切片，不算缺陷。）----
        # assist 标注天然会被多题共享（同一批达人出现在同一份商单里），
        # 那是「辅助参考」而非「指代对象」，不能算指代不清。只看 direct。
        shared = [f"{f}({doc_shared[f]}题)" for f in labels
                  if labels.get(f) == "direct" and doc_shared[f] >= 4
                  and f.startswith("deal_cases/")
                  and it["difficulty"] in ("L1", "L2")]
        if shared:
            rec["flags"]["shared_docs"] = shared
            probs.append(f"商单文档被多题共享，指代不清: {shared}")

        # ---- D 判别力：题干是否字面包含目标文档的类目/品牌/编号 ----
        q = it["query"]
        literal = False
        for f in labels:
            stem = Path(f).stem
            # 从文件名抽可判别标识：类目名（去掉"类目达人刊例表"等后缀）、品牌、DC 编号
            core = re.sub(r"(类目)?达人刊例表|_案例|_跟踪单|^DC\d+", "", stem)
            if core and core in q:
                literal = True
                rec["flags"].setdefault("literal_hits", []).append(core)
            m = re.search(r"DC\d{8}", stem)
            if m and m.group(0) in q:
                literal = True
        rec["flags"]["literal_leak"] = literal
        if literal and it["difficulty"] in ("L2", "L3"):
            probs.append("题干字面包含目标文档类目/品牌名 → 名匹配即可命中，未检验语义改写能力")

        # ---- E 关键词外溢（已去模板串，找疑似漏标）----
        if it["difficulty"] != "L4" and labels:
            keys = [g for g in set(toks(q)) if g not in BOILER and len(g) >= 3]
            rec["flags"]["keywords"] = sorted(keys)[:14]
            hits = Counter()
            for g in keys:
                for f in chunks_per_doc:
                    if g in DOC_TEXT[f]:
                        hits[f] += 1
            # 2 字滑窗（「合理」「价合」「的价」）在几百份文档里天然普现，用它判漏标
            # 必然假阳性。只保留长度 >= 3 的片段作为判别词，并要求命中过半。
            strong = [(f, c) for f, c in hits.most_common(10)
                      if f not in labels and c >= max(3, math.ceil(len(keys) * 0.5))] if keys else []
            rec["flags"]["outside_strong"] = strong[:5]
            if strong:
                probs.append(f"疑似漏标：未标注文档命中 {strong[0][1]}/{len(keys)} 个判别词（{strong[0][0]}）")

        # ---- F 拒答泄漏（已去模板串）----
        if it["difficulty"] == "L4":
            keys = [g for g in set(toks(q)) if g not in BOILER and len(g) >= 2]
            rec["flags"]["keywords"] = sorted(keys)[:14]
            hits = Counter()
            for g in keys:
                for f in chunks_per_doc:
                    if g in DOC_TEXT[f]:
                        hits[f] += 1
            top = hits.most_common(5)
            rec["flags"]["leak_top"] = top
            if top and top[0][1] >= max(4, math.ceil(len(keys) * 0.6)):
                probs.append(f"拒答泄漏嫌疑：{top[0][0]} 命中 {top[0][1]}/{len(keys)} 个判别词")

        # ---- G L3 但实为单文档查找 ----
        if it["difficulty"] == "L3":
            if n_direct <= 1:
                probs.append(f"标为多约束聚合但 direct 文档仅 {n_direct} 个 → 分层可疑")
            cap = min(10, n_direct + sum(1 for v in labels.values() if v == "assist")) / max(1, n_lab)
            rec["flags"]["recall10_cap"] = round(min(1.0, cap), 3)
            if len(labels) > 10:
                probs.append(f"标注 {len(labels)} 篇 > 取回窗口 10 → Recall@10 结构上限 {min(10, len(labels)) / len(labels):.3f}")

        audit["per_item"].append(rec)

    # ---- H 模板簇 ----
    # 模板簇判定要先把实体名抹掉再比：题库是有意做成「同一问法 × 多个实体」的覆盖设计，
    # 「哆哆同学 数据撑得住吗」和「柠檬夫妻 数据撑得住吗」是两道不同的题（真值、答案都不同），
    # 直接按字面比会把它们判成同模板，把有效覆盖算成刷量，掩盖真正的问题（问法单一）。
    from eval_corpus_lib import load_deal_docs, load_rate_cards
    _ent = set()
    try:
        for _d in load_deal_docs():
            for k in ("brand", "category"):
                if _d.get(k):
                    _ent.add(_d[k])
        for _p in load_rate_cards():
            if _p.get("nick"):
                _ent.add(_p["nick"].lstrip("@"))
                _ent.add(_p["nick"])
    except Exception:  # 语料不可达时退化为纯字面比较，不阻断审计
        _ent = set()
    _ENT_SORTED = sorted((e for e in _ent if len(e) >= 2), key=len, reverse=True)

    # 归一化要覆盖题库全部「槽位变量」：业务实体（品牌/昵称/类目）+ 问法参数
    # （投放目标/平台/阶段）。只抹品牌和类目会漏掉 L3 的「类目 × 目标 × 平台」组合，
    # 把它们误判成措辞重复——那些题的真值与答案各不相同，属于有效覆盖。
    SLOT_PHRASES = sorted({
        "种草带货", "推新品", "新品推广", "做口碑测评", "口碑测评", "打品牌曝光", "品牌曝光",
        "哔哩哔哩", "小红书", "抖音", "快手", "视频号", "B 站",
        "刚聊完需求", "还在提案", "已经签约", "正在执行", "已经结案", "最后丢单",
        "需求沟通", "提案", "签约", "执行", "结案", "丢单",
        "还在跑", "在途", "已经收尾", "交付完成", "预算十万以上", "投得比较大",
    }, key=len, reverse=True)

    def skeleton(q: str) -> str:
        """先换问法参数，再换实体名。

        顺序不能反：「口碑测评」里含类目名「测评」，「新品推广」里含「新品」，
        先替换实体会把参数短语切成两半，导致同一问法的骨架判不相等，
        实体变体被误判成措辞雷同。
        """
        s = q
        for w in SLOT_PHRASES:
            s = s.replace(w, "⟨槽位⟩")
        for e in _ENT_SORTED:
            s = s.replace(e, "⟨实体⟩")
        return s

    ids = [(it["id"], tri(it["query"]), it["difficulty"], it["query"], skeleton(it["query"]))
           for it in items]
    _lab = {it["id"]: tuple(sorted(it["labels"])) for it in items}
    pairs = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            sk_a, sk_b = ids[i][4], ids[j][4]
            same_skel = (sk_a == sk_b and "⟨实体⟩" in sk_a)
            v = jac(ids[i][1], ids[j][1]) if same_skel else jac(tri(sk_a), tri(sk_b))
            if v < 0.34:
                continue
            # 三分类：同一真值 = 真重复（同一道题问两遍）；
            # 骨架相同但真值不同 = 实体变体（换品牌/昵称/类目，有效覆盖）；
            # 骨架不同而措辞相近 = 措辞雷同（问法单一，真值也可能不同）。
            same_truth = bool(_lab[ids[i][0]]) and _lab[ids[i][0]] == _lab[ids[j][0]]
            # 短句里槽位占比高（如「{类目}做{目标}的商单有哪些」只剩 12 个字的固定部分），
            # 两条不同骨架的字符相似度天然会到 0.7 以上。这不是措辞雷同，而是
            # 「固定骨架短 + 槽位数多」的算术结果。按槽位密度归一化后再判。
            torso = re.sub(r"⟨[^⟩]+⟩", "", sk_a)
            slot_density = 1 - len(torso) / max(1, len(sk_a))
            if same_truth:
                kind = "真重复"
            elif same_skel:
                kind = "实体变体"
            elif slot_density >= 0.45:
                kind = "短骨架"
            else:
                kind = "措辞雷同"
            pairs.append({"a": ids[i][0], "b": ids[j][0], "jaccard": round(v, 3),
                          "a_query": ids[i][3], "b_query": ids[j][3],
                          "kind": kind, "same_truth": same_truth,
                          "skeleton": sk_a if same_skel else None,
                          "layer": ids[i][2] if ids[i][2] == ids[j][2] else "跨层"})
    # 并查集聚簇：只对「真重复」成对连接。单链接聚类会把「换实体」和「换问法」
    # 串成一个大团（A~B 相似、B~C 相似，但 A 与 C 毫无关系），把有效覆盖算成冗余。
    parent = {it["id"]: it["id"] for it in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p["kind"] != "真重复":
            continue
        ra, rb = find(p["a"]), find(p["b"])
        if ra != rb:
            parent[ra] = rb
    clusters: dict[str, list[str]] = defaultdict(list)
    for it in items:
        clusters[find(it["id"])].append(it["id"])
    dup_clusters = {k: v for k, v in clusters.items() if len(v) > 1}
    audit["template_pairs"] = sorted(pairs, key=lambda x: -x["jaccard"])

    kind_of = {}
    for p in pairs:
        kind_of.setdefault(p["a"], set()).add(p["kind"])
        kind_of.setdefault(p["b"], set()).add(p["kind"])
    clusters = []
    for v in dup_clusters.values():
        kinds = set().union(*[kind_of.get(i, set()) for i in v]) if v else set()
        skel = next((p.get("skeleton") for p in pairs if p["a"] in v and p.get("skeleton")), None)
        if kinds == {"实体变体"}:
            kind = "实体变体"
        elif kinds == {"真重复"}:
            kind = "真重复"
        elif kinds == {"措辞雷同"}:
            kind = "措辞雷同"
        else:
            kind = "混合"
        clusters.append({
            "ids": v, "size": len(v),
            "template": skel or next(i["query"] for i in items if i["id"] == v[0]),
            "kind": kind,
        })
    clusters.sort(key=lambda x: -x["size"])
    audit["template_clusters"] = clusters
    audit["real_dup_clusters"] = [c for c in clusters if c["size"] > 1 and c["kind"] == "真重复"]

    # 实体变体：直接按骨架分组，不走并查集（骨架相同是等价关系，天然成组）
    by_skel: dict[str, list[str]] = defaultdict(list)
    for it, idt in zip(items, ids):
        if "⟨实体⟩" in idt[4] or "⟨槽位⟩" in idt[4]:
            by_skel[idt[4]].append(it["id"])
    audit["entity_variant_clusters"] = [
        {"ids": v, "size": len(v), "template": k, "kind": "实体变体"}
        for k, v in by_skel.items() if len(v) > 1
    ]
    audit["entity_variant_clusters"].sort(key=lambda x: -x["size"])

    # 措辞雷同：只报配对与涉及题数。这类题真值不同，不是重复题，而是「问法不够多样」。
    audit["short_skeleton_pairs"] = [p for p in pairs if p["kind"] == "短骨架"]
    sw = [p for p in pairs if p["kind"] == "措辞雷同"]
    sw_ids = {p["a"] for p in sw} | {p["b"] for p in sw}
    audit["similar_wording_pairs"] = sorted(sw, key=lambda x: -x["jaccard"])
    audit["similar_wording_items"] = sorted(sw_ids)

    # 独立考点数 = 总题数 - 真重复造成的冗余（实体变体/措辞雷同真值不同，不算冗余）
    redundant = sum(c["size"] - 1 for c in audit["real_dup_clusters"])
    audit["distinct_questions"] = len(items) - redundant

    # ---- I 分层配额 ----
    layer_cnt = Counter(it["difficulty"] for it in items)
    # 题库若自带生成时配额（v6 起），就用它作为设计值；否则退回固定设计比例。
    # 写死比例会把「按 70/100/80/50 有意生成」的题库判成全部缺口，是假警报。
    gen_quota = (es.get("generation") or {}).get("quota") or {}
    quota = {}
    for L in DESIGN_QUOTA:
        design_n = gen_quota.get(L)
        ratio = (design_n / len(items)) if design_n else DESIGN_QUOTA[L]
        actual = layer_cnt.get(L, 0) / len(items)
        want = design_n if design_n else round(ratio * len(items))
        quota[L] = {"n": layer_cnt.get(L, 0), "actual_ratio": round(actual, 3),
                    "design_ratio": round(ratio, 3), "gap": layer_cnt.get(L, 0) - want}

    # ---- 汇总 ----
    prob_cnt = Counter()
    for rec in audit["per_item"]:
        for p in rec["problems"]:
            prob_cnt[p.split("：")[0].split("（")[0].split(" →")[0].strip()] += 1
    audit["summary"] = {
        "layer_counts": dict(layer_cnt),
        "quota": quota,
        "independent_questions": audit["distinct_questions"],
        "template_clusters": len(dup_clusters),
        "entity_variant_clusters": len(audit["entity_variant_clusters"]),
        "real_dup_clusters": len(audit["real_dup_clusters"]),
        "real_dup_items": sum(c["size"] for c in audit["real_dup_clusters"]),
        "similar_wording_pairs": len(audit["similar_wording_pairs"]),
        "short_skeleton_pairs": len(audit["short_skeleton_pairs"]),
        "similar_wording_items": len(audit["similar_wording_items"]),
        "items_with_problems": sum(1 for r in audit["per_item"] if r["problems"]),
        "problem_counters": dict(prob_cnt),
        "literal_leak_items": sum(1 for r in audit["per_item"] if r["flags"].get("literal_leak")),
        "recall10_capped_items": sum(1 for r in audit["per_item"]
                                    if r["flags"].get("recall10_cap", 1) < 1),
    }
    OUT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- Markdown ----
    s = audit["summary"]
    L = ["# 测评集质量审计报告", "",
         f"- 评测集：`{EVAL.as_posix().replace(ROOT.as_posix() + '/', '')}`（{es['eval_set_version']}）共 **{len(items)} 题**",
         f"- 语料：{n_docs} 份文档 / {sum(chunks_per_doc.values())} 个文本块",
         f"- 审计方式：确定性规则，不调用大模型。已剔除 {len(BOILER)} 个跨文档模板串（刊例表表头、案例字段名等），避免假阳性",
         "", "## 一、结构性结论", "",
         "| 检查项 | 结果 | 判定 |", "|---|---|---|"]
    L.append(f"| 独立考点数（剔除真重复） | **{s['independent_questions']} / {len(items)}** | 真重复簇 {s['real_dup_clusters']} 个，涉及 {s['real_dup_items']} 题 |")
    L.append(f"| 同一问法换实体的覆盖簇 | {s['entity_variant_clusters']} 个 | 真值不同，属有效覆盖，不计冗余 |")
    L.append(f"| 措辞雷同 | {s['similar_wording_pairs']} 对 / 涉及 {s['similar_wording_items']} 题 | 真值不同但问法相近，属「问法单一」缺陷 |")
    for Lname in ("L1", "L2", "L3", "L4"):
        q = quota.get(Lname)
        if not q:
            continue
        mark = "⚠ 缺口" if abs(q["gap"]) >= 4 else "符合"
        L.append(f"| {Lname} 题量 | {q['n']} 题（实测 {q['actual_ratio']:.1%}，设计 {q['design_ratio']:.0%}） | {mark} |")
    L.append(f"| 题干字面含目标文档标识 | **{s['literal_leak_items']} 题** | 名匹配即可命中，未检验语义能力 |")
    L.append(f"| Recall@10 结构上限 <1 | {s['recall10_capped_items']} 题 | 标注文档数超过取回窗口 |")
    L.append(f"| 存在质量问题的题 | **{s['items_with_problems']} / {len(items)}** | 见第二节 |")
    L += ["", "### 问题类型计数", ""]
    for k, v in sorted(prob_cnt.items(), key=lambda x: -x[1]):
        L.append(f"- {k}：**{v}** 题")
    L += ["", "## 二、真重复簇（同一真值 + 措辞雷同，等于同一道题问两遍）", ""]
    if not audit["real_dup_clusters"]:
        L.append("无。")
    for c in audit["real_dup_clusters"]:
        L.append(f"- **{c['size']} 题真重复**：「{c['template']}」→ {', '.join(c['ids'])}")
    L += ["", "## 二之二、措辞雷同（真值不同，但问法骨架相近，属问法单一缺陷）", ""]
    if not audit["similar_wording_pairs"]:
        L.append("无。")
    for p in audit["similar_wording_pairs"][:20]:
        L.append(f"- 相似度 {p['jaccard']}：`{p['a']}`「{p['a_query']}」 ≈ `{p['b']}`「{p['b_query']}」")
    L += ["", "## 二之三、实体变体簇（同一问法换品牌/昵称/类目，属有效覆盖）", ""]
    for c in audit["entity_variant_clusters"]:
        L.append(f"- {c['size']} 题同问法：「{c['template']}」")
    L += ["", "## 三、逐题问题清单", ""]
    for rec in audit["per_item"]:
        if not rec["problems"]:
            continue
        L.append(f"### {rec['id']}（{rec['layer']}）{rec['query']}")
        for p in rec["problems"]:
            L.append(f"- ⚠ {p}")
        L.append("")
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")

    print(json.dumps({k: v for k, v in s.items() if k != "quota"}, ensure_ascii=False, indent=1))
    print("quota:", json.dumps(quota, ensure_ascii=False))
    print("templates:", json.dumps(audit["template_clusters"], ensure_ascii=False)[:1200])


if __name__ == "__main__":
    main()
