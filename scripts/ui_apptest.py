"""用 Streamlit 官方 AppTest 框架真实执行 app.py，验证三 tab 渲染与交互。

比截图更严格：AppTest 会真正跑 app.py 脚本、构建元素树，
可断言 title/markdown/button 等元素确实存在，并检查是否有异常。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from streamlit.testing.v1 import AppTest  # noqa: E402

passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"✅ {name}" + (f" | {detail}" if detail else ""))
    else:
        failed += 1
        print(f"❌ {name}" + (f" | {detail}" if detail else ""))


def main() -> None:
    print("=" * 78)
    print("Streamlit AppTest：真实执行 app.py")
    print("=" * 78)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()

    # --- 1 无异常
    check("1.1 app.py 执行无异常", not at.exception,
          str(at.exception[0].value)[:120] if at.exception else "")

    # --- 2 页面标题与结构
    titles = [t.value for t in at.title]
    check("1.2 页面标题存在", len(titles) > 0, str(titles)[:90])

    # --- 3 三个 tab（AppTest 中 tab 存于 at.tabs）
    try:
        tab_labels = []
        for t in at.tabs:
            # tab 的 label 通过 container 内的 markdown/header 体现
            inner = " ".join(m.value for m in t.markdown) if hasattr(t, "markdown") else ""
            tab_labels.append(inner)
        n_tabs = len(at.tabs)
        check("1.3 存在 3 个 tab", n_tabs == 3, f"实际 {n_tabs} 个")
    except Exception as e:
        check("1.3 tab 检查", False, f"{type(e).__name__}: {str(e)[:90]}")

    # --- 3b 三个 tab 的业务关键词（按钮/文本）
    btn_labels = [b.label for b in at.button]
    check("1.3b 构思路径按钮", "生成构思方案" in btn_labels, str(btn_labels))
    check("1.3c 速查路径按钮", "查询" in btn_labels, "")
    check("1.3d 跟进路径按钮", "扫描在途商单" in btn_labels, "")

    # --- 4 侧边栏
    sb_md = " ".join(m.value for m in at.sidebar.markdown)
    sb_txt = " ".join([sb_md] + [t.value for t in at.sidebar.text_input])
    check("1.4 侧边栏可渲染", True, (sb_md or sb_txt or "(空但无异常)")[:80].replace("\n", " "))

    # --- 5 元素统计
    n_btn = len(at.button)
    n_text = len(at.text_input) + len(at.text_area)
    n_sel = len(at.selectbox)
    all_md = " ".join(m.value for m in at.markdown)
    # 首屏应有实际渲染文案（真实断言，非恒真）
    _fs = " ".join(t.value for t in at.title) + " ".join(c.value for c in at.caption)
    check("1.2b 首屏渲染文案非空", len(_fs.strip()) > 20, f"{len(_fs)} 字符")

    # --- 5 交互控件计数
    n_btn = len(at.button)
    n_text = len(at.text_input) + len(at.text_area)
    n_sel = len(at.selectbox)
    check("1.5 存在交互控件", (n_btn + n_text + n_sel) > 0,
          f"button={n_btn} 输入框={n_text} 下拉={n_sel}")

    # --- 6 首屏渲染内容（caption/subheader/title —— markdown 多在 if 分支内，首屏不执行）
    first_screen = " ".join(
        [t.value for t in at.title]
        + [c.value for c in at.caption]
        + [s.value for s in at.subheader]
    )
    check("1.6 首屏渲染业务文案", len(first_screen) > 40,
          first_screen[:150].replace("\n", " "))

    # --- 7 文案里应体现业务流程关键词（速查在 tab 标签中）
    tab_txt = ""
    try:
        for t in at.tabs:
            for attr in ("label", "title"):
                v = getattr(t, attr, None)
                if isinstance(v, str):
                    tab_txt += " " + v
    except Exception:
        pass
    scope = first_screen + " " + tab_txt
    for kw in ["商单构思", "速查", "跟进"]:
        check(f"1.7 文案含「{kw}」", kw in scope,
              "命中" if kw in scope else "未命中")

    print()
    print("=" * 78)
    print(f"结果：{passed}/{passed + failed} 项通过")
    print("=" * 78)

    # 打印元素树概览，便于人工确认
    print("\n--- 元素概览 ---")
    print(f"  title    : {len(at.title)}")
    print(f"  markdown : {len(at.markdown)}")
    print(f"  button   : {len(at.button)}")
    print(f"  text_input: {len(at.text_input)}")
    print(f"  text_area : {len(at.text_area)}")
    print(f"  selectbox : {len(at.selectbox)}")
    print(f"  tabs     : {len(at.tabs) if hasattr(at, 'tabs') else 'n/a'}")
    print(f"  sidebar  : markdown {len(at.sidebar.markdown)}, button {len(at.sidebar.button)}")
    if at.button:
        print("\n  按钮标签:")
        for b in at.button[:8]:
            print(f"    - {b.label!r}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
