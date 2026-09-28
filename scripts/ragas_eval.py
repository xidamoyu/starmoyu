"""RAGAS 生成质量评测：faithfulness（忠实度/幻觉率反向指标）+ answer_relevancy（答案相关性）。
数据流：新评测集 54 条 → retriever 检索 top5 → LLM 按真实链路生成回答 → ragas 打分。
结果落盘 reports/ragas_v2.json。
"""
import sys, json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "1")

from starmoyu import llm  # noqa: E402
from starmoyu.retriever import Retriever  # noqa: E402

OUT = ROOT / "reports" / "ragas_v2.json"
EVAL = ROOT / "data" / "eval" / "eval_set.json"
N = 20  # 抽 20 条（RAGAS 每条要多次 LLM 调用，54 条成本高；20 条统计够用）


def gen_answer(query: str, contexts: list[str]) -> str:
    """与生产链路同构的回答生成：只引用检索结果，不知道就说不知道。"""
    ctx = "\n\n".join(f"[{i+1}] {c[:1200]}" for i, c in enumerate(contexts))
    sys_p = (
        "你是 MCN 商单助手。仅依据 <context> 回答，数值必须原样引用 context 中的值；"
        "context 没有的信息明确说「知识库中未找到相关依据」，禁止编造。"
        f"\n<context>\n{ctx}\n</context>"
    )
    resp = llm.chat([{"role": "system", "content": sys_p},
                     {"role": "user", "content": query}])
    return resp


def main():
    from ragas.metrics import faithfulness, answer_relevancy
    from ragas import evaluate as ragas_evaluate
    from datasets import Dataset

    # 适配 ragas 0.2.10: 需要 langchain LLM 包装（用 ARK OpenAI 兼容端点）
    from langchain_openai import ChatOpenAI
    eval_llm = ChatOpenAI(model=llm.CHAT_MODEL, api_key=llm.ARK_API_KEY,
                          base_url=llm.ARK_BASE_URL, temperature=0, max_tokens=2000)
    # answer_relevancy 需要 embedding —— 用本地 Ollama 的 OpenAI 兼容接口
    from langchain_community.embeddings import OllamaEmbeddings
    eval_embeddings = OllamaEmbeddings(model=llm.EMBED_MODEL, base_url=llm.OLLAMA_BASE_URL)

    r = Retriever()
    items = json.loads(EVAL.read_text(encoding="utf-8"))[:N]
    rows = {"question": [], "answer": [], "contexts": []}
    for i, it in enumerate(items):
        out = r.search(it["query"], top_k=5, cand_k=10, use_rerank=True,
                       use_meta_filter=True, use_prior=True, use_dedupe=True, use_quota=True)
        results = out["results"] if isinstance(out, dict) and "results" in out else out
        ctxs = []
        for x in results[:5]:
            if isinstance(x, dict):
                ctxs.append(x.get("content", ""))
            elif hasattr(x, "content"):
                ctxs.append(x.content)
            else:
                ctxs.append(str(x))
        ans = gen_answer(it["query"], ctxs)
        rows["question"].append(it["query"])
        rows["contexts"].append(ctxs)
        rows["answer"].append(ans)
        print(f"[{i+1}/{len(items)}] {it['query'][:30]} -> {len(ans)}字", flush=True)

    ds = Dataset.from_dict(rows)
    res = ragas_evaluate(ds, metrics=[faithfulness, answer_relevancy],
                         llm=eval_llm, embeddings=eval_embeddings)
    scores = res.to_pandas().to_dict("records") if hasattr(res, "to_pandas") else dict(res)
    summary = {k: v for k, v in res.items()} if hasattr(res, "items") else {}
    OUT.write_text(json.dumps({"summary": summary, "rows": scores},
                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n=== RAGAS 结果 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(f"落盘: {OUT}")


if __name__ == "__main__":
    main()
