# -*- coding: utf-8 -*-
"""实验结果落盘守卫：防止同一条实验路线被多次运行覆盖出过期数字。

起因（真实事故）：`experiment_tokenizer.py` 前后跑了三次，
  第一次 带 bug 版本，T2 = 0.392
  第二次 修复版本，  T2 = 0.568
  第三次 一份早前启动的旧脚本副本，T2 = 0.392
三次都写同一个 `reports/experiment_tokenizer.json`。
若第三次最后落盘，磁盘上留下的就是 bug 版数字，而文档里写的是修复版结论 ——
这类「复用过期数字」正是打假纪律列的三大根因之一。

守卫规则：落盘时把脚本自身的 SHA-256 前 12 位写进结果文件。
  · 目标文件不存在                          → 正常写入
  · 目标文件已存在且 script_sha 相同          → 覆盖（同版本重跑，正常）
  · 目标文件已存在但 script_sha 不同          → 先把旧文件改名存档为
    `<原名>.stale-<旧run_date>.json`，再写新文件，并打印告警
这样旧版本的结果永远不会被静默抹掉，也不会占着正式文件名冒充最新结论。

用法：
    from experiment_result_io import save_result
    save_result(OUT, payload, __file__)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def script_sha(script_file: str | Path) -> str:
    """返回脚本内容的 SHA-256 前 12 位，用于判定结果出自哪一版代码。"""
    data = Path(script_file).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def save_result(
    out_path: str | Path,
    payload: dict,
    script_file: str | Path,
    *,
    label: str = "",
) -> Path:
    """写入结果 JSON，并保证不同版本的脚本不会互相覆盖。

    返回实际写入的路径。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sha = script_sha(script_file)
    payload = dict(payload)
    payload["script_sha"] = sha
    payload["script_name"] = Path(script_file).name
    if label:
        payload["script_label"] = label

    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            old = {}
        old_sha = old.get("script_sha")
        if old_sha and old_sha != sha:
            stamp = str(old.get("run_date", "unknown")).replace(":", "").replace(" ", "_")
            backup = out.with_name(f"{out.stem}.stale-{stamp}{out.suffix}")
            n = 1
            while backup.exists():
                backup = out.with_name(f"{out.stem}.stale-{stamp}-{n}{out.suffix}")
                n += 1
            out.rename(backup)
            print(
                f"  ⚠ 旧结果出自另一版脚本（sha {old_sha} → {sha}），"
                f"已存档为 {backup.name}",
                flush=True,
            )
        elif old_sha is None:
            # 旧文件没有 sha 字段：来自引入守卫之前的版本，同样存档保留
            stamp = str(old.get("run_date", "unknown")).replace(":", "").replace(" ", "_")
            backup = out.with_name(f"{out.stem}.legacy-{stamp}{out.suffix}")
            out.rename(backup)
            print(f"  ⚠ 旧结果无版本标记，已存档为 {backup.name}", flush=True)

    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"已落盘 {out.name}（脚本 sha {sha}）", flush=True)
    return out
