"""M7b 迁移：deal_change_requests 商单字段变更审批表。幂等。

背景: 对话内 Agent 发现商单内容变更(预算/负责人等), 无权限直接改 deal 表,
也没有进审批流的通道。新建本表支持「对话发起变更申请 → 审批中心批准 → 自动落 deal 表」。
一商单可挂多条变更(一商单号对应多个修改审批)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from starmoyu import storage  # noqa: E402


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s", (table,))
    return cur.fetchone() is not None


def main() -> None:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        if not table_exists(cur, "deal_change_requests"):
            cur.execute("""
                CREATE TABLE deal_change_requests (
                    request_id    TEXT PRIMARY KEY,
                    deal_id       TEXT NOT NULL,
                    conv_id       TEXT,
                    field_name    TEXT NOT NULL,        -- budget / owner / stage / ...
                    old_value     TEXT,
                    new_value     TEXT,
                    change_summary TEXT,                -- LLM 给的变更说明(原文)
                    status        TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
                    reviewed_by   TEXT,
                    reviewed_at   TIMESTAMPTZ,
                    created_by    TEXT,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dcr_deal ON deal_change_requests(deal_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dcr_status ON deal_change_requests(status)")
            conn.commit()
            print("已建表: deal_change_requests")
        else:
            print("表已存在, 跳过")
    print("OK")


if __name__ == "__main__":
    main()
