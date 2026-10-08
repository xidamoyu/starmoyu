"""M4 迁移：功能 C/D/E/F（对齐清单 / 入库管道 / party_traits / 双模式报价）。

幂等（IF NOT EXISTS / ADD COLUMN IF NOT EXISTS）。
用法：PYTHONPATH=src .venv/Scripts/python.exe scripts/migrate_m4.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402

DDL = [
    # ---- E: 私有经验资产（核心差异化）----
    """
    CREATE TABLE IF NOT EXISTS party_traits (
      trait_id       TEXT PRIMARY KEY,
      party_type     TEXT NOT NULL CHECK (party_type IN ('kol','brand')),
      party_id       TEXT NOT NULL,               -- kol_id 或 brand_name
      trait_category TEXT NOT NULL CHECK (trait_category IN
                        ('沟通偏好','改稿态度','排期习惯','付款要求','内容尺度','其他')),
      trait_content  TEXT NOT NULL,               -- 结构化条目
      source_quote   TEXT,                        -- 原始材料原话（防幻觉关键依据）
      severity       TEXT NOT NULL DEFAULT 'info'
                     CHECK (severity IN ('info','warning','critical')),
      confidence     REAL NOT NULL DEFAULT 1.0,   -- 抽取置信度 0-1
      verified       BOOLEAN NOT NULL DEFAULT FALSE,  -- 仅 TRUE 参与检索
      source_type    TEXT,                        -- 聊天记录/复盘笔记/内部沟通
      created_by     TEXT,
      created_at     TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_traits_party ON party_traits(party_type, party_id) WHERE verified",
    # ---- D: 入库暂存（人工确认前）----
    """
    CREATE TABLE IF NOT EXISTS ingest_staging (
      staging_id     TEXT PRIMARY KEY,
      suggested_kind TEXT NOT NULL CHECK (suggested_kind IN ('kol','deal','feedback')),
      source_type    TEXT NOT NULL,               -- excel/chat/text
      raw_content    TEXT NOT NULL,               -- 原始输入（溯源）
      extracted      JSONB NOT NULL,              -- [{field,value,confidence}]
      status         TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','confirmed','rejected')),
      reviewed_by    TEXT,
      reviewed_at    TIMESTAMPTZ,
      created_by     TEXT,
      created_at     TIMESTAMPTZ DEFAULT now()
    )
    """,
    # ---- F: 双模式报价（placement 三档 + custom；明确不含直播）----
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS coop_models JSONB DEFAULT '[]'",
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS quote_embed_15s INTEGER",
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS quote_embed_30s INTEGER",
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS quote_embed_60s INTEGER",
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS quote_custom INTEGER",
    # 商单实际合作模式
    "ALTER TABLE deal ADD COLUMN IF NOT EXISTS coop_mode TEXT",
    "ALTER TABLE deal ADD COLUMN IF NOT EXISTS embed_duration_sec INTEGER",
]


def main() -> None:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        for i, ddl in enumerate(DDL, 1):
            cur.execute(ddl)
            print(f"  [{i}/{len(DDL)}] OK")
        conn.commit()
        # 验证
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public' ORDER BY table_name""")
        tables = [r[0] for r in cur.fetchall()]
        print(f"\n库表现有 {len(tables)} 张:", ", ".join(tables))
        assert "party_traits" in tables and "ingest_staging" in tables, "M4 表缺失"


if __name__ == "__main__":
    main()
