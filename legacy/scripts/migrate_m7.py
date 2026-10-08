"""M7 数据库全量重建迁移：加列（不重建既有表，幂等）。

达人库新列（Excel 带来 / 用户要求独立）：
  - account_uid          平台账号ID（去重键，不删——删了就无法跨批次去重）
  - contact_mask         脱敏联系方式
  - affiliation_type     挂靠/挂v 独立字段（不并入 coop_models）
  - share_model          分成方式（必需）
  - cpm_21_60s           元（Excel 无 → 生成）
商单/甲方库新列：
  - deal.platform_req / cpm_target / note
  - brand.contact_mask / note
跟进流水已用 deal_followup(action_type='note')，无需改表。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from starmoyu import storage


def _cols(cur, table):
    cur.execute("select column_name from information_schema.columns where table_name=%s", (table,))
    return {r[0] for r in cur.fetchall()}


def _add(cur, table, col, ddl):
    if col not in _cols(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        print(f"  + {table}.{col}")


def main():
    with storage.pg_connect() as c:
        cur = c.cursor()
        kp = _cols(cur, 'kol_profile')
        # 达人库
        _add(cur, 'kol_profile', 'account_uid', 'text')
        _add(cur, 'kol_profile', 'contact_mask', 'text')
        _add(cur, 'kol_profile', 'affiliation_type', 'text')
        _add(cur, 'kol_profile', 'share_model', 'text')
        _add(cur, 'kol_profile', 'cpm_21_60s', 'real')
        # 商单 / 甲方
        _add(cur, 'deal', 'platform_req', 'text')
        _add(cur, 'deal', 'cpm_target', 'real')
        _add(cur, 'deal', 'note', 'text')
        _add(cur, 'brand', 'contact_mask', 'text')
        _add(cur, 'brand', 'note', 'text')
        print('迁移完成。')


if __name__ == '__main__':
    main()
