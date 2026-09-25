"""从 reviewer 子代理的 live transcript 中提取结构化审计报告。"""
from __future__ import annotations

import json
import pathlib

LOG = pathlib.Path(
    r"C:/Users/Administrator/AppData/Local/hermes/cache/delegation/live/deleg_ab9ecd9e/task-0.log"
)
OUT_JSON = pathlib.Path(__file__).resolve().parents[1] / "reports" / "reviewer_audit.json"

BACKSLASH = chr(92)
QUOTE = chr(34)
FIELDS = [
    "verdict_original_requirement",
    "verdict_business_scenario",
    "data_fabrication_check",
    "completeness_check",
    "development_spec_gap",
    "resume_competitiveness",
    "improvement_list",
]


def unescape(s: str) -> str:
    return (s.replace(BACKSLASH + "n", "\n")
             .replace(BACKSLASH + QUOTE, QUOTE)
             .replace(BACKSLASH + BACKSLASH, BACKSLASH))


def grab(name: str, s: str) -> str | None:
    """从一段类 JSON 文本里取出某个顶层字段的字符串值（容错解析）。"""
    marker = QUOTE + name + QUOTE + ":"
    i = s.find(marker)
    if i < 0:
        return None
    i += len(marker)
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s):
        return None
    if s[i] == QUOTE:
        i += 1
        j = i
        while j < len(s):
            ch = s[j]
            if ch == BACKSLASH:
                j += 2
                continue
            if ch == QUOTE:
                break
            j += 1
        return unescape(s[i:j])
    j = i
    while j < len(s) and s[j] not in ",}":
        j += 1
    return s[i:j]


def main() -> None:
    txt = LOG.read_text(encoding="utf-8", errors="replace")
    print(f"transcript 大小: {len(txt)} 字符")

    hits = [ln for ln in txt.splitlines()
            if any(f'"{f}"' in ln for f in FIELDS)]
    print(f"含目标字段的行: {len(hits)}")

    res: dict[str, str] = {}
    for name in FIELDS:
        for h in sorted(hits, key=len, reverse=True):
            v = grab(name, h)
            if v and len(v) > len(res.get(name, "")):
                res[name] = v

    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUT_JSON}")

    for k in FIELDS:
        v = res.get(k, "")
        print("\n" + "=" * 80)
        print(f"### {k}  ({len(v)} 字)")
        print("=" * 80)
        print(v if v else "(未抓到)")


if __name__ == "__main__":
    main()
