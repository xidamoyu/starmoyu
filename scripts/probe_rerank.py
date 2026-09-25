"""验证本地 reranker 可行性：从 modelscope / hf-mirror 下载 bge-reranker-v2-m3 并真实推理。

背景：Ollama 0.34.2 无原生 rerank 接口（/api/rerank 404），
      ollama 官方库中也没有 bge-reranker 系列模型（且 registry.ollama.ai 网络不可达）。
      因此本地 rerank 通过 sentence-transformers 直接加载 CrossEncoder 实现。
"""
import os
import sys
import time

# 走国内镜像，避免 huggingface 直连超时
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")


def main() -> None:
    print(f"[1] 准备加载 CrossEncoder: {MODEL}")
    print(f"    HF_ENDPOINT={os.environ.get('HF_ENDPOINT')}")
    t0 = time.time()
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as e:
        print(f"    ❌ 缺少依赖: {e}")
        return

    try:
        model = CrossEncoder(MODEL, max_length=512, device="cuda" if _cuda() else "cpu")
    except Exception as e:
        print(f"    ❌ 加载失败: {type(e).__name__}: {str(e)[:300]}")
        print("    回退尝试 modelscope 下载 ...")
        if not _via_modelscope(MODEL):
            return
        model = CrossEncoder(_local_dir(MODEL), max_length=512, device="cuda" if _cuda() else "cpu")
    print(f"    ✅ 加载完成，耗时 {time.time()-t0:.1f}s，设备={model.model.device if hasattr(model,'model') else '?'}")

    query = "美妆类目 10 到 50 万粉的腰部达人报价大概什么价位"
    docs = [
        "【美妆类目达人刊例表】K0025 美妆日常 小红书 腰部 442.8w 精华护肤 ¥32,800 ¥59,700 ¥135,200",
        "【商单立项与执行 SOP】预算分配建议：达人费用占 75%-85%，内容制作 5%-10%",
        "【抖音星图下单与结算规则】订单创建后，达人须在 48 小时内确认接单，逾期自动取消。",
        "【在途商单跟踪单】罗纹内衣 计划在小红书投放服饰类目产品，目标是曝光",
    ]
    print(f"\n[2] 推理测试（query={query[:20]}…）")
    t0 = time.time()
    scores = model.predict([(query, d) for d in docs], show_progress_bar=False)
    cost = (time.time() - t0) * 1000
    print(f"    延迟: {cost:.0f} ms / {len(docs)} 条（{cost/len(docs):.0f} ms/条）")
    for d, s in sorted(zip(docs, scores), key=lambda x: -x[1]):
        print(f"    {float(s):+.4f}  {d[:60]}")
    top = max(zip(docs, scores), key=lambda x: x[1])[0]
    ok = "刊例表" in top
    print(f"\n[3] 排序正确性: {'✅ 正确命中刊例表' if ok else '❌ 未命中期刊例表（Top1=' + top[:40] + '）'}")
    print(f"\n结论：本地 rerank {'可用' if ok else '可用但排序待调'}")


def _cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _local_dir(m: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "modelscope", m.replace("/", "_"))


def _via_modelscope(m: str) -> bool:
    try:
        from modelscope import snapshot_download
    except ImportError:
        os.system(f'"{sys.executable}" -m pip install modelscope -q')
        try:
            from modelscope import snapshot_download
        except ImportError as e:
            print(f"    ❌ modelscope 安装失败: {e}")
            return False
    try:
        p = snapshot_download(m, cache_dir=os.path.join(os.path.expanduser("~"), ".cache", "modelscope"))
        print(f"    ✅ modelscope 下载完成: {p}")
        os.environ["_DS_LOCAL"] = p
        return True
    except Exception as e:
        print(f"    ❌ modelscope 下载失败: {str(e)[:200]}")
        return False


if __name__ == "__main__":
    main()
