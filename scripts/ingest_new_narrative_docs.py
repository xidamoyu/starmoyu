# -*- coding: utf-8 -*-
"""把新增的叙述性语料增量入库（单文档回流，不走会 TRUNCATE kol_profile 的 ingest.main）。

为什么需要这个脚本：
    `src/starmoyu/ingest.py` 的 main() 会 TRUNCATE kol_profile / brand / deal /
    parent_chunk / chunk_meta 后全量重建，直接跑会洗掉达人库。
    新增文档必须走 `sediment_service.ingest_document_incremental`，
    它按 source_file 幂等（同文档重入先删旧块再插新块），且带幂等自检。

用法：
    .venv/Scripts/python.exe -u scripts/ingest_new_narrative_docs.py
产物：
    reports/ingest_narrative_docs.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from server.sediment_service import ingest_document_incremental  # noqa: E402
from starmoyu.ingest import parse_markdown  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "reports" / "ingest_narrative_docs.json"

# 本轮新增的叙述性语料（散文体复盘/实录，非条目式清单）
NEW_DOCS = [
    # 第一批：6 篇 33 块
    "playbook/类目复盘-美妆.md",
    "playbook/议价过程实录与判断依据.md",
    "playbook/类目复盘-母婴与3C.md",
    "playbook/达人评估的经验判断.md",
    "policies/商单事故复盘汇编.md",
    "policies/客户沟通与需求澄清实录.md",
    # 第二批：17 篇 150 块
    "playbook/投放目标与达人结构匹配.md",
    "playbook/平台生态差异与跨平台投放.md",
    "playbook/脚本创作与审核实操.md",
    "playbook/数据指标定义与常见误读.md",
    "playbook/达人库维护与标签体系.md",
    "playbook/报价体系与成本结构.md",
    "playbook/排期管理与延期处理.md",
    "playbook/结案复盘与资产沉淀.md",
    "policies/达人合作风险与合同要点.md",
    "policies/项目立项与资源分配.md",
    "playbook/内容形态选择与创意方法.md",
    "policies/客户类型差异与应对.md",
    "playbook/直播投放与坑位管理.md",
    "playbook/大促节点投放节奏.md",
    "policies/新人培养与执行标准.md",
    "playbook/类目复盘-食品与家居.md",
    "playbook/类目复盘-服饰游戏与汽车.md",
]


def main() -> None:
    rows = []
    t0 = time.time()
    for i, rel in enumerate(NEW_DOCS, 1):
        p = RAW / rel
        if not p.exists():
            print(f"[{i}/{len(NEW_DOCS)}] 跳过（文件不存在）{rel}", flush=True)
            continue
        content = p.read_text(encoding="utf-8")
        title, secs = parse_markdown(content)
        n_chars = sum(len(b) for _, b in secs)
        t1 = time.time()
        res = ingest_document_incremental(rel, title, content, flush=True)
        rows.append({
            "source_file": rel, "title": title, "sections": len(secs),
            "chars": n_chars, "ok": res.get("ok"),
            "chunks": res.get("chunks"), "parents": res.get("parents"),
            "error": res.get("error"), "seconds": round(time.time() - t1, 1),
        })
        print(f"[{i}/{len(NEW_DOCS)}] {rel} → 块 {res.get('chunks')} / 父块 "
              f"{res.get('parents')} / 用时 {time.time()-t1:.0f}s", flush=True)

    ok = sum(1 for r in rows if r["ok"])
    total_chunks = sum(r["chunks"] or 0 for r in rows)
    payload = {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "docs": len(rows), "docs_ok": ok,
        "new_chunks": total_chunks,
        "total_seconds": round(time.time() - t0, 1),
        "note": "叙述性语料增量入库，散文体复盘与实录，非条目式清单",
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n=== 入库结果 ===")
    print(f"  文档 {ok}/{len(rows)} 成功｜新增子块 {total_chunks}｜总耗时 {time.time()-t0:.0f}s")
    print(f"已落盘 {OUT.name}")


if __name__ == "__main__":
    main()
