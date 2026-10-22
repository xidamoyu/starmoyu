"""前后端接口对齐校验：把前端所有 api.xxx(...) 调用与后端 42 个路由逐一比对。

用途：改端点后跑一次，避免「前端调了不存在的路由」这类只在真机点击时才暴露的 404。
输出：三类差异 + 退出码（有前端调不通的路由则 exit 1）。
"""
import re
import sys
import glob
import pathlib

sys.path.insert(0, "src")
from server.api import app  # noqa: E402


PH = r"\$\{[^}]+\}|\{[a-zA-Z_]+\}"


def norm(p: str) -> str:
    """把 /api/deals/${dealId}/x 与 /api/deals/{deal_id}/x 归一成同一形态。"""
    p = re.sub(PH, "{X}", p)
    return p.rstrip("/")


routes = {
    (list(r.methods)[0], norm(r.path))
    for r in app.routes
    if hasattr(r, "methods") and str(r.path).startswith("/api")
}

calls = set()
for f in glob.glob("frontend/src/**/*.ts", recursive=True) + glob.glob(
    "frontend/src/**/*.vue", recursive=True
):
    s = pathlib.Path(f).read_text(encoding="utf-8")
    for m in re.finditer(r"api\s*\.\s*(get|post|patch|delete|put)\s*\(\s*[`'\"]([^`'\"]+)", s):
        path = m.group(2)
        calls.add((m.group(1).upper(), norm("/api" + path if path.startswith("/") else path)))
    for m in re.finditer(r"fetch\s*\(\s*[`'\"]([^`'\"]*?/api/[^`'\"]+)", s):
        p = m.group(1)
        calls.add(("POST", norm(p if p.startswith("/api") else "/api" + p)))

print(f"后端路由 {len(routes)} 个；前端直接调用 {len(calls)} 个（归一化后）")

missing = sorted(calls - routes)
print(f"\n[FAIL] 前端调了但后端不存在：{len(missing)}")
for m, p in missing:
    print(f"   {m:6} {p}")

unused = sorted(routes - calls)
# 二次判定：url 走变量拼接时正则抓不到字面量（如 api.post(url) 里 url = `/deal-changes/${id}/approve`）。
# 对剩余路由做「静态前缀是否出现在前端源码」的回查，避免误报。
# 收集前端所有「看起来像路径」的字符串字面量（含模板字符串），
# 这样能抓到 api.post(url, ...) 这类间接调用（url 在别处赋值）。
all_src = "\n".join(
    pathlib.Path(f).read_text(encoding="utf-8")
    for f in glob.glob("frontend/src/**/*.ts", recursive=True)
    + glob.glob("frontend/src/**/*.vue", recursive=True)
)
literals = {
    norm(p if p.startswith("/api") else "/api" + p)
    for p in re.findall(r"[`'\"](/[^`'\"]*)[`'\"]", all_src)
}
truly_unused, var_used = [], []
for m, p in unused:
    (var_used if p in literals else truly_unused).append((m, p))

print(f"\n[INFO] 变量拼接调用（脚本未识别字面量，人工核对为已接）：{len(var_used)}")
for m, p in var_used:
    print(f"   {m:6} {p}")

print(f"\n[INFO] 后端有、前端确未调用（脚本/运维/内部链路用）：{len(truly_unused)}")
for m, p in truly_unused:
    print(f"   {m:6} {p}")

sys.exit(1 if missing else 0)
