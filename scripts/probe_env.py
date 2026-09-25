"""环境探测：DeepSeek(ARK) 可用性 + Ollama 模型能力，全部真实调用。"""
import json
import re
import sys
import urllib.request
from pathlib import Path

CFG = Path(r"C:\Users\Administrator\AppData\Local\hermes\config.yaml")


def get_ark_key() -> str:
    import yaml
    d = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    for cp in d.get("custom_providers", []):
        k = cp.get("api_key", "")
        if k.startswith("ark-") and len(k) > 20:
            return k
    m = re.search(r"ark-[0-9a-fA-F-]{20,}", CFG.read_text(encoding="utf-8"))
    return m.group(0) if m else ""


def post(url: str, payload: dict, key: str, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)


def main() -> None:
    key = get_ark_key()
    print(f"[ARK key] 长度={len(key)} 前缀={key[:14]}…")
    if not key:
        print("  ❌ 未找到 ARK key")
        return

    base = "https://ark.cn-beijing.volces.com/api/v3"
    print("\n[火山方舟 · DeepSeek 模型可用性]")
    models = ["deepseek-v4-flash-ga-260731", "deepseek-v4-pro-ga-260813",
              "deepseek-v4-1-flash-260910", "deepseek-v3-1-250821", "deepseek-r1-250528"]
    ok_models = []
    for m in models:
        st, body = post(f"{base}/chat/completions",
                        {"model": m, "messages": [{"role": "user", "content": "回复:ok"}], "max_tokens": 16}, key)
        if st == 200:
            try:
                txt = json.loads(body)["choices"][0]["message"]["content"].strip()
                print(f"  ✅ {m:34s} -> {txt[:40]}")
                ok_models.append(m)
            except Exception:
                print(f"  ⚠️ {m:34s} -> 200 但结构异常")
        else:
            msg = ""
            try:
                msg = json.loads(body).get("error", {}).get("message", body[:120])
            except Exception:
                msg = body[:120]
            print(f"  ❌ {m:34s} -> HTTP {st} {msg[:90]}")

    # 流式（Agent 常用）
    if ok_models:
        print("\n[流式输出测试]")
        st, body = post(f"{base}/chat/completions",
                        {"model": ok_models[0], "messages": [{"role": "user", "content": "数到3"}],
                         "max_tokens": 32, "stream": True}, key)
        print(f"  HTTP {st}，流式片段数={body.count('data:')}，样例={body[:100]!r}")

    # Ollama
    print("\n[Ollama 模型清单]")
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
        for m in tags.get("models", []):
            det = m.get("details", {})
            print(f"  · {m['name']:22s} family={det.get('family')} caps={m.get('capabilities')} "
                  f"ctx={det.get('context_length')} dim={det.get('embedding_length')}")
    except Exception as e:
        print(f"  ❌ {e}")

    # Ollama embedding 实测
    print("\n[Ollama bge-m3 embedding 实测]")
    st, body = post("http://localhost:11434/api/embed",
                    {"model": "bge-m3", "input": ["美妆腰部达人报价"]}, key="")
    if st == 200:
        v = json.loads(body)["embeddings"][0]
        print(f"  ✅ 维度={len(v)} 前5={[round(x,4) for x in v[:5]]}")
    else:
        st2, body2 = post("http://localhost:11434/api/embeddings",
                          {"model": "bge-m3", "prompt": "美妆腰部达人报价"}, key="")
        print(f"  /api/embed -> {st} | /api/embeddings -> {st2} {body2[:120]}")

    # rerank 能力探测（Ollama 原生无 rerank 接口）
    print("\n[Ollama 原生 rerank 接口探测]")
    for ep in ["/api/rerank", "/api/embed"]:
        st, body = post(f"http://localhost:11434{ep}",
                        {"model": "bge-m3", "query": "q", "documents": ["a", "b"]}, key="")
        print(f"  {ep:14s} -> HTTP {st} {body[:110]}")


if __name__ == "__main__":
    main()
