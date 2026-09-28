"""检索层：Milvus 向量召回 + PostgreSQL/BM25 关键词召回 → RRF 融合 → DashScope 云端 Rerank 重排。

支持消融实验开关：use_bm25 / use_vector / use_rrf / use_rerank / use_meta_filter / use_prior / use_dedupe
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import jieba
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import llm, storage  # noqa: E402

STOP = set("的 了 和 是 在 我 有 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这 那 与 及 或 对 中 为 以 被 把 让 给 从 并 等 之 其 该 将 可以 需要 应该 如何 什么 怎么".split())

CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def parse_fans_range(query: str) -> tuple[int, int] | None:
    q = query.replace("粉丝", "粉").replace(" ", "")
    m = re.search(r"(\d+)\s*(?:到|~|-|至)\s*(\d+)\s*(万|w|W|百万)?粉?", q)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        unit = m.group(3)
        mult = 1_0000 if unit in ("万", "w", "W") else (100_0000 if unit == "百万" else 1)
        a, b = lo * mult, hi * mult
        return (min(a, b), max(a, b))
    m = re.search(r"(\d+)\s*(万|w|W)?粉?(以上|以下|以内)", q)
    if m:
        v = int(m.group(1)) * (1_0000 if m.group(2) else 1)
        return (v, 10 ** 12) if m.group(3) == "以上" else (0, v)
    m = re.search(r"([一二两三四五六七八九十百]+)万粉", q)
    if m:
        return (cn_to_int(m.group(1)) * 1_0000, 10 ** 12)
    return None


def cn_to_int(s: str) -> int:
    if s in CN_NUM:
        return CN_NUM[s]
    total, cur = 0, 0
    for ch in s:
        if ch == "十":
            cur = (cur or 1) * 10
        elif ch == "百":
            cur = (cur or 1) * 100
        elif ch in CN_NUM:
            cur += CN_NUM[ch]
    return total + cur


def extract_fans_from_text(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(w|W|万)\s*(?:粉|粉丝)?", text)
    if m:
        return int(float(m.group(1)) * 1_0000)
    m = re.search(r"粉丝量[：:\s]*(\d[\d,]*)", text)
    return int(m.group(1).replace(",", "")) if m else None


def extract_all_fans(text: str) -> list[int]:
    """抽取块内所有粉丝量。刊例表一表多行，只看第一个数会误杀整块。"""
    out: list[int] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(w|W|万)\s*(?:粉|粉丝)?", text):
        out.append(int(float(m.group(1)) * 1_0000))
    for m in re.finditer(r"粉丝量[：:\s]*(\d[\d,]*)", text):
        out.append(int(m.group(1).replace(",", "")))
    return out


@dataclass
class Chunk:
    chunk_id: str
    parent_id: str
    doc_id: str
    doc_title: str
    doc_type: str
    source_file: str
    object_key: str
    section: str
    chunk_type: str
    content: str
    category: str = ""
    platform: str = ""
    tier: str = ""
    deal_year: int = 0
    score: float = 0.0
    rank_vector: int | None = None
    rank_bm25: int | None = None
    rank_rerank: int | None = None

    def to_dict(self) -> dict:
        return {"chunk_id": self.chunk_id, "doc_title": self.doc_title, "doc_type": self.doc_type,
                "source_file": self.source_file, "object_key": self.object_key,
                "section": self.section, "chunk_type": self.chunk_type,
                "score": round(self.score, 4), "rank_vector": self.rank_vector,
                "rank_bm25": self.rank_bm25, "rank_rerank": self.rank_rerank,
                "category": self.category, "preview": self.content[:120].replace("\n", " ")}


class Retriever:
    """Milvus(向量) + PostgreSQL(元数据/父块) 双存储检索器。"""

    def __init__(self, quota_per_type: int = 3):
        self.pg = storage.pg_connect(storage.pg_ensure_database())
        self.milvus = storage.milvus_client()
        self.quota_per_type = quota_per_type   # 高先验文档类型的最低候选名额
        self._load_chunks()
        self._build_bm25()

    def _load_chunks(self) -> None:
        cols = ("chunk_id,parent_id,doc_id,doc_title,doc_type,source_file,object_key,section,"
                "chunk_type,category,platform,tier,deal_year,content")
        rows = self.pg.execute(f"SELECT {cols} FROM chunk_meta").fetchall()
        self.chunks: list[Chunk] = []
        self.by_id: dict[str, Chunk] = {}
        for r in rows:
            c = Chunk(chunk_id=r[0], parent_id=r[1], doc_id=r[2], doc_title=r[3], doc_type=r[4],
                      source_file=r[5], object_key=r[6], section=r[7], chunk_type=r[8],
                      category=r[9] or "", platform=r[10] or "", tier=r[11] or "",
                      deal_year=r[12] or 0, content=r[13])
            self.chunks.append(c)
            self.by_id[c.chunk_id] = c

    def _tokens(self, text: str) -> list[str]:
        toks = [t.strip() for t in jieba.cut(text) if len(t.strip()) >= 2 and t.strip() not in STOP]
        toks += [ch for ch in re.findall(r"[\u4e00-\u9fff]", text) if ch not in STOP]
        return toks

    def _build_bm25(self) -> None:
        from rank_bm25 import BM25Okapi
        self.corpus_tokens = [self._tokens(c.content) for c in self.chunks]
        self.bm25 = BM25Okapi(self.corpus_tokens)

    # ---- Milvus 向量召回
    def retrieve_vector(self, query: str, top_k: int) -> list[tuple[str, float]]:
        qv = llm.embed([query])[0]
        hits = self.milvus.search(storage.COLLECTION, data=[qv], limit=top_k,
                                  output_fields=["chunk_id"])
        out = []
        for h in hits[0]:
            cid = h.get("id") or (h.get("entity") or {}).get("chunk_id")
            if cid:
                out.append((cid, float(h.get("distance", 0.0))))
        return out

    # ---- BM25 召回（语料来自 PostgreSQL）
    def retrieve_bm25(self, query: str, top_k: int) -> list[tuple[str, float]]:
        toks = self._tokens(query)
        if not toks:
            return []
        scores = self.bm25.get_scores(toks)
        order = np.argsort(-scores)[:top_k]
        return [(self.chunks[i].chunk_id, float(scores[i])) for i in order if scores[i] > 0]

    # ---- 查询条件解析与元数据过滤
    def parse_query_cond(self, query: str) -> dict:
        cond: dict = {}
        for cat in ["美妆", "母婴", "3C数码", "3c数码", "食品饮料", "服饰", "家居", "汽车", "游戏"]:
            if cat in query:
                cond["category"] = cat.replace("3c", "3C")
                break
        for pf in ["抖音", "小红书", "快手"]:
            if pf in query:
                cond["platform"] = pf
                break
        for t in ["头部", "腰部", "尾部"]:
            if t in query:
                cond["tier"] = t
                break
        fans = parse_fans_range(query)
        if fans:
            cond["fans"] = fans
        return cond

    def meta_filter(self, query: str, candidates: list[str]) -> tuple[list[str], dict]:
        cond = self.parse_query_cond(query)
        if not cond:
            return candidates, {}
        kept = []
        for cid in candidates:
            c = self.by_id.get(cid)
            if c is None:
                continue
            ok = True
            for k, v in cond.items():
                if k == "fans":
                    nums = extract_all_fans(c.content)
                    if not nums:
                        continue
                    lo, hi = v
                    if not any(lo <= n <= hi for n in nums):
                        ok = False
                        break
                    continue
                cv = getattr(c, k, "")
                if not cv:
                    continue
                if v not in cv:
                    ok = False
                    break
            if ok:
                kept.append(cid)
        return (kept, cond) if len(kept) >= 3 else (candidates, cond)

    @staticmethod
    def doc_type_prior(query: str) -> dict[str, float]:
        if any(k in query for k in ("报价", "刊例", "价位", "多少钱", "价格")):
            return {"rate_card": 2.0, "deal_case": 0.6, "inflight_deal": 0.4}
        if any(k in query for k in ("案例", "做过", "成功经验", "复盘", "roi", "ROI")):
            return {"deal_case": 2.0, "inflight_deal": 0.5}
        if any(k in query for k in ("规则", "政策", "能不能", "合规", "红线", "资质", "流程", "SOP")):
            return {"platform_policy": 1.8, "methodology": 0.6}
        if any(k in query for k in ("怎么选", "策略", "方法论", "方法", "技巧", "框架", "建议", "怎么分配")):
            return {"methodology": 1.8, "platform_policy": 0.5}
        if any(k in query for k in ("在途", "跟进", "卡住", "停滞", "进度")):
            return {"inflight_deal": 2.0}
        return {}

    @staticmethod
    def rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
        score: dict[str, float] = {}
        for rl in rank_lists:
            for rank, cid in enumerate(rl):
                score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank + 1)
        return score

    @staticmethod
    def dedupe_by_doc(r: "Retriever", cids: list[str], max_per_doc: int = 2) -> list[str]:
        """按 doc_id 限流：避免同构文档用数量优势淹没高相关单篇文档。
        上限自适应文档大小：大文档（如 17-19 块的刊例大表）内部各块是互补内容，
        固定 max_per_doc=2 会误杀深处块（v1 小文档时代遗留的死值），故取
        max(max_per_doc, 文档总块数的一半)。跨文档限流逻辑不变。
        """
        cnt: dict[str, int] = {}
        doc_total: dict[str, int] = {}
        for c in r.by_id.values():
            doc_total[c.doc_id] = doc_total.get(c.doc_id, 0) + 1
        out: list[str] = []
        for cid in cids:
            c = r.by_id.get(cid)
            if c is None:
                continue
            d = c.doc_id
            cap = max(max_per_doc, doc_total.get(d, 1) // 2)
            if cnt.get(d, 0) >= cap:
                continue
            cnt[d] = cnt.get(d, 0) + 1
            out.append(cid)
        return out

    # ---- 云端 Rerank（DashScope gte-rerank-v2）
    def rerank(self, query: str, candidates: list[str], top_n: int) -> tuple[list[str], bool]:
        docs = [self.by_id[c].content for c in candidates if c in self.by_id]
        if not docs:
            return candidates[:top_n], False
        try:
            ranked = llm.rerank(query, docs, top_n=None)
            out = []
            for idx, sc in ranked:
                if 0 <= idx < len(candidates):
                    cid = candidates[idx]
                    self.by_id[cid].score = sc
                    out.append(cid)
            for c in candidates:
                if c not in out:
                    out.append(c)
            return out[:top_n], True
        except Exception:
            return candidates[:top_n], False

    # ---- 主检索入口
    def search(self, query: str, top_k: int = 5, cand_k: int = 20, *,
               use_bm25: bool = True, use_vector: bool = True, use_rrf: bool = True,
               use_rerank: bool = True, use_meta_filter: bool = True,
               use_prior: bool = True, use_dedupe: bool = True,
               use_quota: bool = True, max_per_doc: int = 2,
               rerank_cand_k: int | None = None) -> dict:
        """检索主入口。

        cand_k        多路召回的池宽（向量 / BM25 各取 cand_k 条），越大召回越全。
        rerank_cand_k 送入 reranker 的候选数（默认取 RERANK_CAND_K 环境变量，
                      未设置则等于 cand_k）。实测：GT 文档在候选池 top-5 内命中率 100%，
                      故收窄此值可大幅降低重排耗时且不损失精度。
        """
        if rerank_cand_k is None:
            _env = os.environ.get("RERANK_CAND_K", "").strip()
            rerank_cand_k = int(_env) if _env.isdigit() else cand_k
        info: dict = {"query": query, "rerank_used": False, "meta_cond": {}, "filtered": 0,
                      "cand_k": cand_k, "rerank_cand_k": rerank_cand_k}

        vec_hits = self.retrieve_vector(query, cand_k) if use_vector else []
        bm_hits = self.retrieve_bm25(query, cand_k) if use_bm25 else []
        info["n_vector"] = len(vec_hits)
        info["n_bm25"] = len(bm_hits)

        if use_meta_filter:
            merged: list[str] = []
            for c, _ in vec_hits + bm_hits:
                if c not in merged:
                    merged.append(c)
            kept, cond = self.meta_filter(query, merged)
            info["meta_cond"] = cond
            if cond:
                keep_set = set(kept)
                info["filtered"] = len(merged) - len(keep_set)
                merged = [c for c in merged if c in keep_set] + [c for c in merged if c not in keep_set]
            order = {c: i for i, c in enumerate(merged)}
            vec_hits = sorted(vec_hits, key=lambda x: order.get(x[0], 10 ** 6))
            bm_hits = sorted(bm_hits, key=lambda x: order.get(x[0], 10 ** 6))

        vec_list = [c for c, _ in vec_hits]
        bm_list = [c for c, _ in bm_hits]
        for i, cid in enumerate(vec_list):
            if cid in self.by_id:
                self.by_id[cid].rank_vector = i + 1
        for i, cid in enumerate(bm_list):
            if cid in self.by_id:
                self.by_id[cid].rank_bm25 = i + 1

        if use_dedupe:
            vec_list = self.dedupe_by_doc(self, vec_list, max_per_doc=max_per_doc)
            bm_list = self.dedupe_by_doc(self, bm_list, max_per_doc=max_per_doc)
            info["after_dedupe"] = {"vector": len(vec_list), "bm25": len(bm_list)}

        if use_vector and use_bm25 and use_rrf:
            fused = self.rrf([vec_list, bm_list])
            cands = sorted(fused, key=lambda c: -fused[c])
        elif use_vector and use_bm25:
            cands = vec_list + [c for c in bm_list if c not in set(vec_list)]
        elif use_vector:
            cands = vec_list
        else:
            cands = bm_list

        if use_prior:
            prior = self.doc_type_prior(query)
            info["type_prior"] = prior
            if prior:
                base = {c: 1.0 / (i + 1) for i, c in enumerate(cands)}
                cands = sorted(cands, key=lambda c: -base[c] * prior.get(self.by_id[c].doc_type, 0.35))

        # 类型配额：先验高优先的类型必须进入候选窗口。
        # 原因：当某类同构文档（如大量在途跟踪单）以数量占满候选窗口时，
        # 单靠排序加权无法救回篇数极少但高相关的文档（如刊例表），
        # 因此为其保留最低名额，再按原序填充其余名额。
        if use_prior and use_quota and info.get("type_prior"):
            prior = info["type_prior"]
            strong = [t for t, w in prior.items() if w >= 1.5]
            if strong:
                reserved: list[str] = []
                for t in strong:
                    got = [c for c in cands if self.by_id[c].doc_type == t][:self.quota_per_type]
                    reserved.extend(got)
                if reserved:
                    rest = [c for c in cands if c not in set(reserved)]
                    cands = reserved + rest
                    info["quota_reserved"] = {t: sum(1 for c in reserved if self.by_id[c].doc_type == t)
                                              for t in strong}

        cands = [c for c in cands[:cand_k] if c in self.by_id]

        if use_rerank and cands:
            # 收窄送入 reranker 的候选规模。
            # 依据：实测 GT 文档在重排前的候选池 top-5 内命中率 100%（54/54），
            # 即整个候选池大部分条目对最终 Top-K 无贡献，却要付出等量重排耗时。
            # 配额保底产生的候选位于队首，因此截断不会丢失高先验类型。
            if rerank_cand_k and rerank_cand_k < len(cands):
                info["rerank_skipped"] = len(cands) - rerank_cand_k
                cands = cands[:rerank_cand_k]
            info["rerank_n_cands"] = len(cands)
            # 取完整重排结果（不先截断），否则高先验类型排在 top_k 之外时无法被提升
            ordered_all, used = self.rerank(query, cands, len(cands))
            info["rerank_used"] = used
            ordered = ordered_all
            # 保障高先验类型可见性：
            # 重排模型会被同构文档里的字面相似片段（如跟踪单中的"报价 ¥58,300"）误导，
            # 仅靠候选配额不足以保证其进入最终 Top-K，故在完整结果上做一次提升。
            if use_quota and info.get("type_prior"):
                strong = [t for t, w in info["type_prior"].items() if w >= 1.5]
                if strong:
                    bumped = []
                    for t in strong:
                        for cid in ordered:
                            if self.by_id[cid].doc_type == t:
                                bumped.append(cid)
                                break
                    if bumped:
                        ordered = bumped + [c for c in ordered if c not in set(bumped)]
                        info["quota_promoted"] = [self.by_id[c].doc_type for c in bumped]
            ordered = ordered[:top_k]
        else:
            ordered = cands[:top_k]

        for i, cid in enumerate(ordered):
            self.by_id[cid].rank_rerank = i + 1

        return {"info": info, "results": [self.by_id[c] for c in ordered]}

    # ---- 父块扩展与上下文组装
    def expand_to_parent(self, chunk_ids: list[str]) -> str:
        seen, texts = set(), []
        for cid in chunk_ids:
            c = self.by_id.get(cid)
            if not c or c.parent_id in seen:
                continue
            seen.add(c.parent_id)
            row = self.pg.execute("SELECT content FROM parent_chunk WHERE parent_id=%s",
                                  (c.parent_id,)).fetchone()
            texts.append(row[0] if row else c.content)
        return "\n\n".join(texts)

    def build_context(self, chunks: list[Chunk], max_chars: int = 4000) -> tuple[str, list[dict]]:
        parts, cites, used = [], [], 0
        for i, c in enumerate(chunks, 1):
            block = f"[{i}] 【{c.doc_title}】{c.section}\n{c.content}"
            if used + len(block) > max_chars and parts:
                break
            parts.append(block)
            used += len(block)
            cites.append({"n": i, "doc_title": c.doc_title, "section": c.section,
                          "source_file": c.source_file, "object_key": c.object_key,
                          "doc_type": c.doc_type, "category": c.category,
                          "deal_year": c.deal_year})
        return "\n\n".join(parts), cites

    def close(self) -> None:
        for obj in (self.pg, self.milvus):
            try:
                obj.close()
            except Exception:
                pass
