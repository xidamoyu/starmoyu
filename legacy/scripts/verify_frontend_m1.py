"""前端链路冒烟：Vite 页面 + 代理到后端的完整链路。

用法（后端 :8000 与 Vite :5173 都启动后）：
  .venv/Scripts/python.exe scripts/verify_frontend_m1.py
"""
import sys

import httpx

ok = True


def check(name, cond, detail=""):
    global ok
    ok = ok and cond
    print(f"  {'✅' if cond else '❌'} {name}" + (f" | {detail}" if detail else ""))


def main():
    print("== 前端链路冒烟 ==")

    r = httpx.get("http://localhost:5173/", timeout=10)
    check("1 Vite 服务前端页面", r.status_code == 200, f"HTTP {r.status_code}")

    r2 = httpx.get("http://localhost:5173/api/health", timeout=15)
    check("2 Vite 代理 /api → :8000", r2.status_code == 200, f"HTTP {r2.status_code}")

    r3 = httpx.post("http://localhost:5173/api/auth/login",
                    json={"username": "admin", "password": "admin123"}, timeout=15)
    check("3 代理登录", r3.status_code == 200, f"HTTP {r3.status_code}")
    if r3.status_code != 200:
        sys.exit(1)
    tok = r3.json()["token"]

    r4 = httpx.post("http://localhost:5173/api/conversations",
                    json={"title": "前端链路验证"},
                    headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    check("4 代理建会话", r4.status_code == 200, str(r4.json())[:40])

    print("\n结果:", "全部通过 ✅" if ok else "存在失败 ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
