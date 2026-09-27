"""M5 迁移：扩展 ingest_staging 的 kind 枚举（brief / deal_result），幂等。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from starmoyu import storage  # noqa: E402


def main() -> None:
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname='ingest_staging_suggested_kind_check'")
        row = cur.fetchone()
        if row and "brief" in row[0] and "deal_result" in row[0]:
            print("已迁移，跳过")
            return
        print("当前约束:", row[0] if row else "(无)")
        cur.execute("ALTER TABLE ingest_staging DROP CONSTRAINT ingest_staging_suggested_kind_check")
        cur.execute("""ALTER TABLE ingest_staging ADD CONSTRAINT ingest_staging_suggested_kind_check
                       CHECK (suggested_kind = ANY (ARRAY['kol'::text, 'deal'::text,
                       'feedback'::text, 'brief'::text, 'deal_result'::text]))""")
        conn.commit()
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname='ingest_staging_suggested_kind_check'")
        print("新约束:", cur.fetchone()[0])
    print("OK")


if __name__ == "__main__":
    main()
