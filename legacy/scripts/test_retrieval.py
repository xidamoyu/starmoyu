"""检索链路冒烟测试：确认各路召回 + RRF + 重排 + 引用组装全部可用。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from starmoyu.retriever import Retriever  # noqa: E402

QUERIES = [
    "美妆类目 10 到 50 万粉的腰部达人报价大概什么价位",
    "商单立项的预算怎么分配，达人费用占比多少",
    "我们做过哪些美妆精华类的成功案例，ROI 怎么样",
    "头部达人的档期一般要提前多久锁定",
    "小红书美妆投放的注意事项和合规红线",
]


def main() -> None:
    r = Retriever()
    print(f"索引就绪：chunk={len(r.chunks)}（向量存于 Milvus，元数据存于 PostgreSQL）\n")
    for q in QUERIES:
        out = r.search(q, top_k=5)
        info = out["info"]
        print("=" * 100)
        print(f"Q: {q}")
        print(f"  召回: 向量={info['n_vector']} BM25={info['n_bm25']} 元数据过滤={info['meta_cond']} "
              f"过滤数={info['filtered']} 重排生效={info['rerank_used']}")
        for i, c in enumerate(out["results"], 1):
            print(f"  [{i}] score={c.score:.3f} v_rank={c.rank_vector} b_rank={c.rank_bm25} "
                  f"| {c.doc_title[:34]} | {c.section[:22]} | {c.chunk_type}")
            print(f"      {c.content[:88].replace(chr(10), ' ')}")
        ctx, cites = r.build_context(out["results"], max_chars=1500)
        print(f"  上下文长度={len(ctx)} 引用数={len(cites)}")
        print(f"  首条引用 -> {cites[0]['doc_title']} / {cites[0]['source_file']}")
    r.close()


if __name__ == "__main__":
    main()
