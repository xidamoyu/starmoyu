"""M6 迁移：party_traits 加 deal_id 溯源列 + conversations 加 pinned_deal_id，幂等。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from starmoyu import storage  # noqa: E402


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name=%s AND column_name=%s""", (table, column))
    return cur.fetchone() is not None


def main() -> None:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        changed = []
        if not column_exists(cur, "party_traits", "deal_id"):
            cur.execute("ALTER TABLE party_traits ADD COLUMN deal_id TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_traits_deal ON party_traits(deal_id)")
            changed.append("party_traits.deal_id")
        if not column_exists(cur, "conversations", "pinned_deal_id"):
            cur.execute("ALTER TABLE conversations ADD COLUMN pinned_deal_id TEXT")
            changed.append("conversations.pinned_deal_id")
        conn.commit()
        if changed:
            print("已加列:", ", ".join(changed))
        else:
            print("已迁移，跳过")
    print("OK")


if __name__ == "__main__":
    main()
