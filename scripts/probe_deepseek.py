"""验证 ARK Code Plan 端点能否用于 DeepSeek chat，并探测可用模型。"""
import json
import urllib.request
import re
from pathlib import Path

CFG = Path(r"C:\Users\Administrator\AppData\Local\hermes\config.yaml")


def key() -> str:
    import yaml
    d = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    return d["custom_providers"][0]["api_key"]


def call(url: str, model: str, key: str, stream=False, timeout=60):
    payload = {"model": model, "messages": [{"role": "user", "content": "只回复两个字：可用"}],
               "max_tokens": 20}
    if stream:
        payload["stream"] = True
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)


def main() -> None:
    k = key()
    plan = "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions"
    std = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

    print("=== A. Code Plan 端点 /api/plan/v3 ===")
    for m in ["ark-code-latest", "deepseek-v4-flash-ga-260731", "deepseek-v4-pro-ga-260813"]:
        st, body = call(plan, m, k)
        info = body[:200]
        if st == 200:
            try:
                info = json.loads(body)["choices"][0]["message"]["content"][:60]
            except Exception:
                pass
        print(f"  {m:34s} -> HTTP {st} | {info}")

    print("\n=== B. Code Plan 端点模型列表 ===")
    for ep in ["https://ark.cn-beijing.volces.com/api/plan/v3/models",
               "https://ark.cn-beijing.volces.com/api/v3/models"]:
        req = urllib.request.Request(ep, headers={"Authorization": f"Bearer {k}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            ids = [x.get("id") for x in d.get("data", [])]
            print(f"  {ep} -> {len(ids)} 个模型")
            for i in ids[:25]:
                print(f"     · {i}")
            ds = [i for i in ids if "deepseek" in str(i).lower()]
            print(f"     其中 deepseek 相关: {ds}")
        except urllib.error.HTTPError as e:
            print(f"  {ep} -> HTTP {e.code} {e.read().decode('utf-8','replace')[:120]}")
        except Exception as e:
            print(f"  {ep} -> {e}")


if __name__ == "__main__":
    main()
