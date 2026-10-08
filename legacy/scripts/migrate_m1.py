"""M1 数据库迁移：新增 7 张表 + kol_profile 档期字段。

设计见 docs/REBUILD-PLAN.md §3。
- 幂等：CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS（PG 9.6+ 支持）
- 复用现有 storage.pg_connect()，与业务库同一实例

用法：
  .venv/Scripts/python.exe scripts/migrate_m1.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starmoyu import storage  # noqa: E402

DDL = [
    # ---- 用户与认证 ----
    """
    CREATE TABLE IF NOT EXISTS users (
      user_id       TEXT PRIMARY KEY,
      username      TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      display_name  TEXT,
      role          TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('admin','operator','viewer')),
      created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 对话 ----
    """
    CREATE TABLE IF NOT EXISTS conversations (
      conv_id        TEXT PRIMARY KEY,
      user_id        TEXT NOT NULL REFERENCES users(user_id),
      title          TEXT,
      created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
      msg_id       TEXT PRIMARY KEY,
      conv_id      TEXT NOT NULL REFERENCES conversations(conv_id),
      role         TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
      content      TEXT,
      tool_calls   JSONB,
      tool_call_id TEXT,
      created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, created_at)",
    # ---- 方案（业务沉淀核心）----
    """
    CREATE TABLE IF NOT EXISTS proposals (
      proposal_id      TEXT PRIMARY KEY,
      conv_id          TEXT,
      deal_id          TEXT REFERENCES deal(deal_id),
      requirement_text TEXT NOT NULL,
      content          TEXT NOT NULL,
      status           TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','pending_review','approved','rejected')),
      created_by       TEXT REFERENCES users(user_id),
      created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at       TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS proposal_versions (
      version_id  TEXT PRIMARY KEY,
      proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
      version_no  INT NOT NULL,
      content     TEXT NOT NULL,
      comment     TEXT,
      changed_by  TEXT,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- 达人档期与排他（MCN 业务核心约束）----
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS exclusive_until DATE",
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS available_from  DATE",
    "ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS blacklist       TEXT",
    # ---- 跟进动作增强 ----
    "ALTER TABLE deal_followup ADD COLUMN IF NOT EXISTS action_type TEXT",
    "ALTER TABLE deal_followup ADD COLUMN IF NOT EXISTS operator_id TEXT REFERENCES users(user_id)",
]

SEED_ADMIN = """
INSERT INTO users (user_id, username, password_hash, display_name, role)
VALUES (%s, %s, %s, %s, 'admin')
ON CONFLICT (username) DO NOTHING
"""


def main() -> None:
    print("=" * 70)
    print(f"M1 迁移  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 70)

    applied = 0
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        for i, stmt in enumerate(DDL, 1):
            head = " ".join(stmt.split())[:56]
            cur.execute(stmt)
            applied += 1
            print(f"  [{i:02d}] OK  {head}…")
        conn.commit()

        # 种子管理员（密码在下面单独 hash，避免 SQL 明文）
        print("\n-- 种子账号 --")
        from passlib.hash import bcrypt
        cur.execute(SEED_ADMIN, ("u-admin", "admin",
                                 bcrypt.hash("admin123"), "管理员"))
        print("  admin / admin123（首次登录后应改密）")
        conn.commit()

    # ---- 验证 ----
    print("\n-- 迁移后验证 --")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""select table_name from information_schema.tables
                       where table_schema='public' order by 1""")
        tables = [r[0] for r in cur.fetchall()]
        print(f"  表数量: {len(tables)}  ->  {', '.join(tables)}")
        cur.execute("""select column_name from information_schema.columns
                       where table_name='kol_profile'
                         and column_name in ('exclusive_until','available_from','blacklist')""")
        cols = [r[0] for r in cur.fetchall()]
        print(f"  kol_profile 档期字段: {cols}")
        cur.execute("select count(*) from users")
        print(f"  users 种子: {cur.fetchone()[0]} 条")

    expect = {"users", "conversations", "messages", "proposals", "proposal_versions"}
    missing = expect - set(tables)
    if missing:
        print(f"\n❌ 缺表: {missing}")
        sys.exit(1)
    if len(cols) != 3:
        print("\n❌ kol_profile 档期字段不全")
        sys.exit(1)
    print("\n✅ M1 迁移完成")


if __name__ == "__main__":
    main()
