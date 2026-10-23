# -*- coding: utf-8 -*-
"""归因：语料扩充后 26 道题首位命中丢失，是否由新增叙述性语料挤占造成。

背景：语料 1809 → 1974 块（+165 块方法论/政策叙述性内容）后，
同配置（A5/P0，等权 RRF + 先验 + 配额）Hit@1 从 0.680 降到 0.616，
净损失 16 题（26 掉、10 涨）。L2 层最严重（-12.0pt）。

本脚本对每道掉下来的题重跑检索，看新语料的块是否进入了 top-10，
以及真值文档被挤到了第几位。目的不是找借口，是判断该修语料还是修检索。

用法：
    .venv/Scripts/python.exe -u scripts/diagnose_corpus_intrusion.py
产物：
    reports/diagnose_corpus_intrusion.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import starmoyu.llm as llm  # noqa: E402
import starmoyu.retriever as R  # noqa: E402
from experiment_result_io import save_result  # noqa: E402

EVAL = ROOT / "reports" / "eval_set_v6.json"
NEW_RES = ROOT / "reports" / "eval_corpus_expansion.json"
OLD_RES = ROOT / "reports" / "retrieval_eval_v6_results.json"
OUT = ROOT / "reports" / "diagnose_corpus_intrusion.json"
TOP_K = 10

_EMB_CACHE: dict = {}
_orig = llm.embed


def cached_embed(texts, **kw):
    key = tuple(texts)
    if key not in _EMB_CACHE:
        _EMB_CACHE[key] = _orig(texts, **kw)
    return _EMB_CACHE[key]


llm.embed = cached_embed

# 本轮新增的 23 篇叙述性文档
NEW_DOCS = {
    "playbook/类目复盘-美妆.md", "playbook/议价过程实录与判断依据.md",
    "playbook/类目复盘-母婴与3C.md", "playbook/达人评估的经验判断.md",
    "policies/商单事故复盘汇编.md", "policies/客户沟通与需求澄清实录.md",
    "playbook/投放目标与达人结构匹配.md", "playbook/平台生态差异与跨平台投放.md",
    "playbook/脚本创作与审核实操.md", "playbook/数据指标定义与常见误读.md",
    "playbook/达人库维护与标签体系.md", "playbook/报价体系与成本结构.md",
    "playbook/排期管理与延期处理.md", "playbook/结案复盘与资产沉淀.md",
    "policies/达人合作风险与合同要点.md", "policies/项目立项与资源分配.md",
    "playbook/内容形态选择与创意方法.md", "policies/客户类型差异与应对.md",
    "playbook/直播投放与坑位管理.md", "playbook/大促节点投放节奏.md",
    "policies/新人培养与执行标准.md", "playbook/类目复盘-食品与家居.md",
    "playbook/类目复盘-服饰游戏与汽车.md",
}


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    by_id = {i["id"]: i for i in es["items"]}
    old = json.loads(OLD_RES.read_text(encoding="utf-8"))
    new = json.loads(NEW_RES.read_text(encoding="utf-8"))
    k_new = "P0_现行生产_等权RRF_含先验配额"
    o = {x["id"]: x for x in old["per_item_detail"]["A5_完整管线(含真重排序)"]}
    n = {x["id"]: x for x in new["per_item_detail"][k_new]}

    dropped = [i for i in o if o[i]["hit1"] >= 1 and n[i]["hit1"] < 1]
    gained = [i for i in o if o[i]["hit1"] < 1 and n[i]["hit1"] >= 1]
    print(f"掉下来 {len(dropped)} 题｜新命中 {len(gained)} 题", flush=True)

    r = R.Retriever()
    rows = []
    for i, qid in enumerate(dropped, 1):
        it = by_id[qid]
        out = r.search(it["query"], top_k=TOP_K, cand_k=30)
        seq, seen = [], set()
        for c in out["results"]:
            if c.source_file not in seen:
                seen.add(c.source_file)
                seq.append(c.source_file)
            if len(seq) >= TOP_K:
                break
        labels = set(it["labels"])
        rank_truth = next((j + 1 for j, s in enumerate(seq) if s in labels), None)
        intruders = [s for s in seq if s in NEW_DOCS]
        rows.append({
            "id": qid, "layer": it["difficulty"], "query": it["query"],
            "truth_rank": rank_truth, "n_labels": len(labels),
            "new_doc_in_top10": len(intruders),
            "intruders": intruders, "top10": seq,
        })
        if i % 5 == 0:
            print(f"  已跑 {i}/{len(dropped)}", flush=True)

    n_intrude = sum(1 for x in rows if x["new_doc_in_top10"] > 0)
    n_truth_in10 = sum(1 for x in rows if x["truth_rank"] is not None)
    print(f"\n=== 归因结果 ===")
    print(f"  掉下来的 {len(rows)} 题中：")
    print(f"    真值仍在 top-10 内：{n_truth_in10} 题（被挤到首位之外）")
    print(f"    真值已掉出 top-10：{len(rows)-n_truth_in10} 题")
    print(f"    top-10 含新叙述性语料：{n_intrude} 题")
    print(f"    平均新语料占位：{sum(x['new_doc_in_top10'] for x in rows)/max(1,len(rows)):.2f} / 10")

    by_layer: dict = {}
    for x in rows:
        d = by_layer.setdefault(x["layer"], {"n": 0, "intrude": 0, "truth_in10": 0})
        d["n"] += 1
        d["intrude"] += 1 if x["new_doc_in_top10"] > 0 else 0
        d["truth_in10"] += 1 if x["truth_rank"] is not None else 0
    print("\n=== 分层 ===")
    for L, d in sorted(by_layer.items()):
        print(f"  {L}: {d['n']} 题｜新语料占位 {d['intrude']} 题｜真值仍在 top10 {d['truth_in10']} 题")

    save_result(OUT, {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dropped": len(dropped), "gained": len(gained),
        "net": len(gained) - len(dropped),
        "dropped_with_truth_in_top10": n_truth_in10,
        "dropped_with_new_doc_in_top10": n_intrude,
        "avg_new_doc_slots": round(sum(x["new_doc_in_top10"] for x in rows) / max(1, len(rows)), 2),
        "by_layer": by_layer, "rows": rows,
        "note": "语料扩充后首位命中丢失的归因；判断该修语料还是修检索",
    }, __file__, label="corpus-intrusion")


if __name__ == "__main__":
    main()
