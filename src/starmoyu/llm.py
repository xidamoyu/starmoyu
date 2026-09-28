"""LLM 接入层：DeepSeek(火山方舟 ARK) + 本地 Ollama Embedding + 阿里云 DashScope Rerank。

三个通道各自独立、可配置：
  - Chat     : DeepSeek via 火山方舟 ARK（OpenAI 兼容）
  - Embedding: 本地 Ollama bge-m3（1024 维）
  - Rerank   : 阿里云 DashScope gte-rerank-v2（云端 API）

Rerank 通道演进记录（面试可讲）：
  最初用本地 sentence-transformers CrossEncoder（bge-reranker-v2-m3），
  但实测在本机 CPU 上 1126 ms/条，一次 54 题消融需 60+ 分钟；
  尝试 GPU 加速失败 —— GTX 1650 Ti 仅 4GB 显存，Windows 桌面(DWM/Edge)已占 3.6GB，
  fp32 权重 2.3GB 直接 OOM，fp16 后显存耗尽反而降到 3225 ms/条（比 CPU 慢 3 倍）。
  结论：4GB 显存不足以承载该模型，改用云端 rerank API，
  本地零依赖（可卸载 3.9GB 的 torch），且省去模型加载时间。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx


def _load_env() -> None:
    """从项目根 .env 加载配置（不覆盖已有环境变量）。"""
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

# ---------------------------------------------------------------- 配置

ARK_BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3")
ARK_API_KEY = os.environ.get("ARK_API_KEY", "")
CHAT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash-ga-260731")
CHAT_MODEL_PRO = os.environ.get("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro-ga-260813")

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# 注：默认值必须用 127.0.0.1 而非 localhost——Windows 下 localhost 可能解析为
# IPv6 ::1，而 Ollama 只监听 IPv4，SYN_SENT 永远悬起（消融进程曾因此卡死 25 分钟）。
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "16"))

DASHSCOPE_RERANK_URL = os.environ.get(
    "DASHSCOPE_RERANK_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank")
DASHSCOPE_API_KEY = os.environ.get("QWEN_API_KEY", "")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "gte-rerank-v2")
RERANK_BATCH = int(os.environ.get("RERANK_BATCH", "20"))   # DashScope 单次文档数上限
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "60"))

_RERANK_LOCK = threading.Lock()


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------- Chat（DeepSeek / ARK）

def _post(url: str, payload: dict, headers: dict, timeout: float = 120.0, retries: int = 4) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers,
                                   content=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                last = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:400]}")
        except httpx.HTTPError as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise LLMError(f"重试 {retries} 次仍失败: {last}")


def chat(messages: list[dict], temperature: float = 0.3, max_tokens: int = 2048,
         json_mode: bool = False, model: str | None = None) -> str:
    """调用 DeepSeek（火山方舟 ARK）Chat 接口。"""
    if not ARK_API_KEY:
        raise LLMError("ARK_API_KEY 未设置（DeepSeek 通道）")
    payload: dict[str, Any] = {
        "model": model or CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _post(f"{ARK_BASE_URL}/chat/completions", payload,
                 {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"})
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"响应结构异常: {json.dumps(data, ensure_ascii=False)[:300]}") from e


def chat_json(messages: list[dict], temperature: float = 0.0, max_tokens: int = 2048,
              model: str | None = None) -> dict:
    """调用 Chat 并解析 JSON（容错围栏与截断）。"""
    raw = chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True, model=model)
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                pass
        fixed = _repair_truncated(text)
        if fixed is not None:
            return fixed
        raise LLMError(f"无法解析 JSON: {text[:300]}")


def _close_brackets(body: str) -> str:
    """补齐未闭合的字符串与括号，使片段成为合法 JSON。"""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in body:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    out = body + ('"' if in_str else "")
    for ch in reversed(stack):
        out += "]" if ch == "[" else "}"
    return out


def _repair_truncated(text: str) -> dict | None:
    """修复被 max_tokens 截断的 JSON：从尾部回退到最近的元素边界再补括号。"""
    s = text.find("{")
    if s == -1:
        return None
    body = text[s:]
    candidates = [i for i, ch in enumerate(body) if ch in ",}]"]
    if not candidates:
        candidates = [len(body) - 1]
    for i in reversed(candidates):
        frag = body[:i + 1].rstrip()
        if frag.endswith(","):
            frag = frag[:-1]
        try:
            obj = json.loads(_close_brackets(frag))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------- Embedding（本地 Ollama）

def embed(texts: list[str], batch_size: int = EMBED_BATCH, dim: int = EMBED_DIM) -> list[list[float]]:
    """本地 Ollama 向量化，保持输入顺序，并校验维度。"""
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = [t if t.strip() else "空" for t in texts[i:i + batch_size]]
        data = _post(f"{OLLAMA_BASE_URL}/api/embed",
                     {"model": EMBED_MODEL, "input": batch},
                     {"Content-Type": "application/json"}, timeout=300.0)
        embs = data.get("embeddings") or []
        if not embs:
            raise LLMError(f"Ollama embed 返回空: {json.dumps(data, ensure_ascii=False)[:200]}")
        out.extend(embs)
    if out and len(out[0]) != dim:
        raise LLMError(f"向量维度不匹配：期望 {dim}，实际 {len(out[0])}（检查 EMBED_MODEL）")
    return out


def embed_available() -> tuple[bool, str]:
    try:
        r = httpx.post(f"{OLLAMA_BASE_URL}/api/embed",
                       json={"model": EMBED_MODEL, "input": ["探针"]}, timeout=30)
        if r.status_code == 200:
            v = r.json()["embeddings"][0]
            return True, f"{EMBED_MODEL} dim={len(v)}"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:100]}"


# ---------------------------------------------------------------- Rerank（阿里云 DashScope）

def rerank(query: str, docs: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    """DashScope gte-rerank-v2 重排，返回 [(原始索引, 分数)]，按分数降序。

    超过 RERANK_BATCH 条时自动分批，并对各批分数做全局归并 —— 
    注意 DashScope 的 relevance_score 是「批内相对」还是「绝对」会影响跨批可比性，
    因此统一在最后按分数全局排序，而不是按批拼接。
    """
    if not docs:
        return []
    if not DASHSCOPE_API_KEY:
        raise LLMError("缺少 QWEN_API_KEY，无法调用 DashScope rerank")

    scored: dict[int, float] = {}
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
    for start in range(0, len(docs), RERANK_BATCH):
        batch = docs[start:start + RERANK_BATCH]
        payload = {
            "model": RERANK_MODEL,
            "input": {"query": query, "documents": [d[:2000] for d in batch]},
            "parameters": {"return_documents": False, "top_n": len(batch)},
        }
        data = _post(DASHSCOPE_RERANK_URL, payload, headers, timeout=RERANK_TIMEOUT)
        results = (data.get("output") or {}).get("results") or []
        if not results:
            raise LLMError(f"rerank 返回为空: {str(data)[:200]}")
        for item in results:
            scored[start + int(item["index"])] = float(item["relevance_score"])

    ranked = sorted(scored.items(), key=lambda kv: -kv[1])
    return ranked[:top_n] if top_n else ranked


def rerank_available() -> tuple[bool, str]:
    """探测 DashScope rerank 是否就绪（真实发起一次极小请求）。"""
    if not DASHSCOPE_API_KEY:
        return False, "缺少 QWEN_API_KEY"
    try:
        out = rerank("探针查询", ["文档A", "文档B"], top_n=2)
        return True, f"{RERANK_MODEL} 可用，返回 {len(out)} 条评分"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def channels_health() -> dict:
    """三通道健康检查。"""
    out: dict = {}
    try:
        t = chat([{"role": "user", "content": "回复:ok"}], max_tokens=8)
        out["chat"] = {"ok": True, "model": CHAT_MODEL, "reply": t.strip()[:20]}
    except Exception as e:
        out["chat"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    ok, msg = embed_available()
    out["embedding"] = {"ok": ok, "detail": msg}
    ok2, msg2 = rerank_available()
    out["rerank"] = {"ok": ok2, "detail": msg2}
    return out


if __name__ == "__main__":
    print(json.dumps(channels_health(), ensure_ascii=False, indent=2))
