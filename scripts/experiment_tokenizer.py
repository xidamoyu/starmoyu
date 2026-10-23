# -*- coding: utf-8 -*-
"""重构方向验证：分词与词权重修正。

诊断确认的第四个缺陷（也是最具体的）：
  tokenizer 在 jieba 词组之外，额外把每个汉字都塞进 token 列表
  （retriever.py:133 `toks += [ch for ch in re.findall(r"[\\u4e00-\\u9fff]", text)]`）。
  对「小羊同学 粉丝多少、属于哪一档量级？」这一句，产出 21 个 token，
  其中 13 个是单字噪声（小 羊 同 学 粉 丝 多 少 属 于 哪 档 量 级）。
  这直接把 BM25 的 IDF 信号稀释掉：昵称「小羊同学」被切成「小羊」+「同学」，
  而「同学」「粉丝」在几百篇文档里都出现，区分度趋近于零。

本实验对比几种分词与词权重方案（全部走 BM25 单路 + 文档级输出，看纯粹的召回质量变化）：

  T0 现状：jieba 词 + 单字
  T1 纯 jieba 词（>=2 字，去停用词）
  T2 纯 jieba 词 + 昵称保护（把问句里出现在刊例表中的昵称整体作为一个 token）
  T3 T2 + 业务词降权（粉丝/报价/CPM 这类词在几百篇里都出现，按 df 倒数降权）

用法：.venv/Scripts/python.exe -u scripts/experiment_tokenizer.py
产物：reports/experiment_tokenizer.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import jieba
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from starmoyu.retriever import Retriever, STOP  # noqa: E402
from eval_v5_retrieval import metrics_for_ranked  # noqa: E402

EVAL = ROOT / "reports" / "eval_set_v6.json"
OUT = ROOT / "reports" / "experiment_tokenizer.json"
TOP_K = 10


def tok_now(text: str) -> list[str]:
    """现状：jieba 词 + 所有汉字。"""
    toks = [t.strip() for t in jieba.cut(text) if len(t.strip()) >= 2 and t.strip() not in STOP]
    toks += [ch for ch in re.findall(r"[\u4e00-\u9fff]", text) if ch not in STOP]
    return toks


def tok_pure(text: str) -> list[str]:
    """纯 jieba 词。"""
    return [t.strip() for t in jieba.cut(text) if len(t.strip()) >= 2 and t.strip() not in STOP]


def main() -> None:
    es = json.loads(EVAL.read_text(encoding="utf-8"))
    items = [i for i in es["items"] if i["difficulty"] != "L4" and i["labels"]]
    r = Retriever()
    doc_of = {c.chunk_id: c.source_file for c in r.chunks}
    print(f"语料 {len(r.chunks)} 块 / {len(set(doc_of.values()))} 篇｜题目 {len(items)} 道", flush=True)

    from rank_bm25 import BM25Okapi

    # 昵称表必须先抽出来：语料侧切词也要用同一张表，
    # 否则查询侧把「小羊同学」当一个 token、语料侧切成「小羊」「同学」，
    # 两边对不上，BM25 直接零分（初版实验就栽在这里）。
    nicks: set[str] = set()
    for c in r.chunks:
        if c.doc_type != "rate_card":
            continue
        for line in c.content.splitlines():
            for m in re.finditer(r"@([^\s|@]{2,20})", line):
                nicks.add(m.group(1).strip())
    NICKS_SORTED = sorted(nicks, key=len, reverse=True)
    print(f"从刊例表抽出昵称 {len(nicks)} 个", flush=True)

    # 文档频率表（用于 T3 的业务词降权）
    print("建索引 T0（现状：词 + 单字）...", flush=True)
    bm25_0 = BM25Okapi([tok_now(c.content) for c in r.chunks])

    print("建索引 T1（纯 jieba 词）...", flush=True)
    toks1 = [tok_pure(c.content) for c in r.chunks]
    bm25_1 = BM25Okapi(toks1)

    df: dict[str, int] = {}
    for ts in toks1:
        for t in set(ts):
            df[t] = df.get(t, 0) + 1
    N = len(toks1)

    def tok_with_nick(text: str) -> list[str]:
        """把问句与语料里真实出现的昵称整体保留为一个 token，其余正常切词。

        修正记录（两次）：
        ① 初版用 ``n in text`` 全表匹配、且不按长度排序，「西西」这类 2 字昵称
           会在长句里命中多处，把问句切得只剩碎片。
        ② 第二版只对查询侧做昵称保护、语料侧仍用普通切词，导致查询侧产出
           「小羊同学」而语料侧只有「小羊」「同学」，两侧 token 对不上，
           BM25 直接零分（T2 从 0.612 崩到 0.392）。必须两侧同一套切法。
        """
        rest = text
        found: list[str] = []
        for n in NICKS_SORTED:
            if n and n in rest:
                found.append(n)
                rest = rest.replace(n, "\x00")
        toks = tok_pure(rest.replace("\x00", " ")) + found
        return toks

    print("建索引 T2（两侧都做昵称保护）...", flush=True)
    bm25_2 = BM25Okapi([tok_with_nick(c.content) for c in r.chunks])

    cfgs = ["T0_现状词+单字", "T1_纯jieba词", "T2_纯词+昵称保护", "T3_T2+业务词降权"]
    rows_by_cfg: dict[str, list] = {c: [] for c in cfgs}
    t0 = time.time()

    for n, it in enumerate(items, 1):
        q = it["query"]

        def search(bm, toks, weights=None) -> list[str]:
            if not toks:
                return []
            if weights:
                # BM25 的分可分解为各词项独立贡献之和（idf × tf 饱和 × 长度归一）。
                # 逐词单独 get_scores 得到的正是该项在全库上的贡献，
                # 因此「减掉 (1-w) 比例的贡献」等价于按权重 w 缩放该项，量纲不变。
                sc = bm.get_scores(toks).copy()
                for t, w in zip(toks, weights):
                    if w >= 1.0:
                        continue
                    contrib = bm.get_scores([t])
                    sc = sc - contrib * (1.0 - w)
                sc = np.maximum(sc, 0.0)
            else:
                sc = bm.get_scores(toks)
            order = np.argsort(-sc)[:100]
            seq, seen = [], set()
            for i in order:
                if sc[i] <= 0:
                    continue
                d = doc_of.get(r.chunks[i].chunk_id)
                if d and d not in seen:
                    seen.add(d)
                    seq.append(d)
            return seq[:TOP_K]

        # T0 / T1
        r0 = search(bm25_0, tok_now(q))
        r1 = search(bm25_1, tok_pure(q))

        # T2：索引与查询两侧都用昵称保护切词
        tn = tok_with_nick(q)
        r2 = search(bm25_2, tn)

        # T3：对高频业务词降权（df > N*0.02 视为业务泛词）
        weights = []
        for t in tn:
            d = df.get(t, 1)
            w = (min(d, N) / N) ** 0.5 * 10 if d > N * 0.02 else 1.0
            weights.append(1.0 / max(w, 0.01) if d > N * 0.02 else 1.0)
        r3 = search(bm25_2, tn, weights)

        for cfg, ranked in zip(cfgs, (r0, r1, r2, r3)):
            m = metrics_for_ranked(ranked, it["labels"])
            m["id"] = it["id"]
            m["layer"] = it["difficulty"]
            rows_by_cfg[cfg].append(m)

        if n % 50 == 0:
            print(f"  已跑 {n}/{len(items)}（{time.time()-t0:.0f}s）", flush=True)

    results = {}
    for cfg, rows in rows_by_cfg.items():
        agg = {}
        for L in ("ALL", "L1", "L2", "L3"):
            sub = rows if L == "ALL" else [x for x in rows if x["layer"] == L]
            agg[L] = {"n": len(sub)}
            for k in ("hit1", "hit5", "hit10", "mrr", "recall10", "ndcg10"):
                agg[L][k] = round(sum(x[k] for x in sub) / len(sub), 4) if sub else 0.0
        results[cfg] = {"matrix": agg, "per_item": rows}

    OUT.write_text(json.dumps({
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": es["eval_set_version"], "corpus_chunks": len(r.chunks),
        "n_items": len(items), "nicks_extracted": len(nicks),
        "note": "分词方案对比，BM25 单路 + 文档级输出，不含融合与重排序",
        "results": {k: {"matrix": v["matrix"]} for k, v in results.items()},
        "per_item_detail": {k: v["per_item"] for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 分词方案对比（BM25 单路，不含重排序）===")
    for cfg in cfgs:
        a = results[cfg]["matrix"]["ALL"]
        print(f"  {cfg:<22} Hit@1={a['hit1']:.4f} Hit@5={a['hit5']:.4f} "
              f"MRR={a['mrr']:.4f} R@10={a['recall10']:.4f}")
    print("\n=== 分层 Hit@1 ===")
    for cfg in cfgs:
        m = results[cfg]["matrix"]
        print(f"  {cfg:<22} L1={m['L1']['hit1']:.3f} L2={m['L2']['hit1']:.3f} L3={m['L3']['hit1']:.3f}")
    print(f"\n已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
