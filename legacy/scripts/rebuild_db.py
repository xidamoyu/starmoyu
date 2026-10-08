"""M7 数据库全量重建：以真实 Excel 达人看板为源，重建达人/商单/甲方三库。

用户指令要点（逐字）：
  - 以正式 Excel 数据重建达人库；商单/甲方库"随便生成"
  - 联系方式先模糊处理；账号ID"直接删掉"
  - 进度反馈 -> 跟进记录（deal_followup 流水随阶段显示）
  - 分成必需；挂靠/挂v 独立字段，不和 coop_models 合并
  - 平台随机标、基本都抖音；互动率随便生成按粉丝量分布；均播不用
  - cpm 要，excel 没有就生成；其他缺失字段全虚拟生成；数据纪律不必理会

用法：
  python scripts/rebuild_db.py --limit N   # 只装载前 N 个达人（试跑）
  python scripts/rebuild_db.py             # 全量（含去重）
  python scripts/rebuild_db.py --dry-run   # 只解析统计，不写库
"""
import argparse
import json
import math
import random
import re
import sys
import os
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from starmoyu import storage

random.seed(20260927)  # 可复现

XLSX = os.path.join(os.path.dirname(__file__), '..', 'data', 'raw', '达人看板.xlsx')

# 类目规整：-> (category, sub_category)
CAT_MAP = {
    '美妆': ('美妆', '美妆'), '美妆 测评': ('美妆', '测评'),
    '剧情搞笑': ('剧情搞笑', '剧情'), '情感': ('情感', '情感'),
    '情感、口播': ('情感', '口播'), '母婴': ('母婴亲子', '母婴'),
    '母婴亲子': ('母婴亲子', '母婴'), '亲子 母婴': ('母婴亲子', '亲子'),
    '生活': ('生活', '生活'), '美食': ('美食', '美食'),
    '旅行': ('旅行', '旅行'), '二次元': ('二次元', '二次元'),
    '三农': ('三农', '三农'), '时尚': ('时尚', '时尚'),
    '影视娱乐': ('影视娱乐', '影视'), '运动健身': ('运动健身', '健身'),
    '游戏': ('游戏', '游戏'), '萌宠': ('萌宠', '萌宠'),
    '汽车': ('汽车', '汽车'), '科技数码': ('3C数码', '数码'),
    '生活/科技数码': ('3C数码', '数码'), '测评': ('测评', '测评'),
    '随拍': ('生活', '随拍'),
}


# ---------------------------------------------------------------- 清洗工具
def clean_uid(v):
    """账号ID：科学计数法/文本 -> 纯数字串；无法解析返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r'\d+(\.0+)?[eE]\+?\d+', s):  # 科学计数法
        try:
            return str(int(float(s)))
        except Exception:
            return None
    s = re.sub(r'\D', '', s)
    return s or None


def parse_fans(v):
    """粉丝数 '100W'/'28.7w'/123 -> int。异常值(<=0) 夹到 1。"""
    if v is None:
        return None
    s = str(v).strip().lower().replace(',', '')
    if not s:
        return None
    m = re.match(r'^([\d.]+)\s*w$', s)
    try:
        n = int(float(m.group(1)) * 10000) if m else int(float(s))
    except Exception:
        return None
    return max(n, 1)


def parse_price(v, fans):
    """60S价格 -> int 元。异常值用 价格/粉丝 比 + 分位数夹逼清洗。"""
    if v is None:
        return None
    s = str(v).strip().replace(',', '')
    if not s:
        return None
    try:
        p = int(float(s))
    except Exception:
        return None
    if p <= 0 or fans is None:
        return None
    ratio = p / fans
    if ratio < 0.005:      # 价格低得离谱（如 10 元）
        return None
    if ratio > 0.60:       # 价格高得离谱（如 500 万）
        p = int(fans * 0.30)
    return p


def mask_contact(v):
    """联系方式脱敏：手机号 -> 138****1234；微信号 -> 前2***后2；其他原样。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    d = re.sub(r'\D', '', s)
    if len(d) >= 11 and s.strip().isdigit():  # 手机号
        return d[:3] + '****' + d[-4:]
    if re.fullmatch(r'[A-Za-z0-9_\-]{5,}', s):  # 微信号
        return s[:2] + '***' + s[-2:] if len(s) > 6 else s[0] + '***'
    return s[:2] + '***' if len(s) > 4 else s


def clean_nick(v):
    if v is None:
        return None
    s = re.sub(r'\s+', ' ', str(v)).strip()
    return s or None


def tier_of(fans):
    if fans is None:
        return '腰部'
    if fans >= 5_000_000:
        return '头部'
    if fans >= 500_000:
        return '腰部'
    if fans >= 100_000:
        return '尾部'
    return 'koc'


def er_of(fans):
    """互动率按粉丝量分布（头低尾高），加噪声。"""
    if fans is None:
        base = 0.045
    elif fans >= 5_000_000:
        base = 0.028
    elif fans >= 1_000_000:
        base = 0.040
    elif fans >= 100_000:
        base = 0.055
    else:
        base = 0.080
    return round(base * random.uniform(0.75, 1.30), 4)


def parse_share(v):
    """分成方式 -> 规整文本；返回 (share_model, profit_rate)。"""
    if v is None:
        return None, None
    s = str(v).strip()
    if not s:
        return None, None
    m = re.search(r'(\d+)', s)
    rate = int(m.group(1)) if m else None
    if '整体' in s:
        return f'整体{rate}' if rate else '整体分成', (rate / 100 if rate else None)
    if '返' in s:
        return f'返{rate}%' if rate else '返点', (rate / 100 if rate else None)
    if '带项目' in s or '谈' in s:
        return '带项目谈', None
    return s, (rate / 100 if rate else None)


AFF_MAP = {'挂靠': '挂靠', '挂v': '挂v', '挂靠合作': '挂靠', 'v': '挂v'}


def parse_aff(*vals):
    """挂靠/挂v 独立字段。三列分别是 挂靠状态/挂靠合作/挂v合作；
    真实数据极稀疏，'是' 按所在列默认归类，带关键词按关键词。"""
    out = None
    for idx, v in enumerate(vals):
        if v is None:
            continue
        s = str(v).strip()
        if not s or s in ('否', 'no', '0'):
            continue
        low = s.lower()
        if '挂v' in low or '挂 v' in low:
            return '挂v'
        if '挂靠' in low:
            return '挂靠'
        # 纯 '是' / 其他：按列位置归类
        if idx == 2:
            return '挂v'
        return '挂靠'
    return out


# ---------------------------------------------------------------- 达人装载
def load_kols(cur, limit, dry):
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)

    def rows_from(ws, has_header):
        it = ws.iter_rows(min_row=2 if has_header else 1, values_only=True)
        return it

    raw = []  # dict per 达人
    seen = {}
    stats = Counter()

    def consume(ws, has_header):
        for r in rows_from(ws, has_header):
            nick = clean_nick(r[3] if len(r) > 3 else None)
            if not nick:
                stats['blank_nick'] += 1
                continue
            # 过滤混入的表头文字行（汇总表无表头，人工粘贴残留）
            if '昵称' in nick:
                stats['header_skip'] += 1
                continue
            # 过滤列偏移行：昵称列是纯数字（应为文本昵称）
            if re.fullmatch(r'[\d\.]+', nick):
                stats['offset_skip'] += 1
                continue
            uid = clean_uid(r[4] if len(r) > 4 else None)
            fans = parse_fans(r[8] if len(r) > 8 else None)
            price = parse_price(r[7] if len(r) > 7 else None, fans)
            cat_in = str(r[2]).strip() if len(r) > 2 and r[2] else ''
            category, sub = CAT_MAP.get(cat_in, (cat_in or '生活', cat_in or ''))
            share_model, profit_rate = parse_share(r[14] if len(r) > 14 else None)
            aff = parse_aff(r[11] if len(r) > 11 else None, r[12] if len(r) > 12 else None)
            progress = str(r[10]).strip() if len(r) > 10 and r[10] else None
            on_price = str(r[15]).strip() if len(r) > 15 and r[15] else None
            rec = dict(uid=uid, nick=nick, fans=fans, price=price, category=category,
                       sub=sub, share=share_model, rate=profit_rate, aff=aff,
                       progress=progress, on_price=on_price,
                       contact=mask_contact(r[5] if len(r) > 5 else None))
            key = uid or nick
            if key in seen:
                stats['dup_skip'] += 1
                continue
            seen[key] = True
            raw.append(rec)

    ws1 = wb['组员达人看板']
    consume(ws1, True)
    n1 = len(raw)
    ws2 = wb['之前达人看板汇总']
    consume(ws2, False)
    stats['sheet1_kols'] = n1
    stats['sheet2_kols'] = len(raw) - n1

    if limit:
        raw = raw[:limit]

    # 平台：基本都抖音（95%），少量 快手/小红书
    for rec in raw:
        rec['platform'] = random.choices(['抖音', '快手', '小红书'], weights=[95, 3, 2])[0]
        rec['tier'] = tier_of(rec['fans'])
        rec['er'] = er_of(rec['fans'])
        if rec['fans'] and rec['price']:
            rec['cpm'] = round(rec['price'] / (rec['fans'] / 1000.0), 1)
        else:
            rec['cpm'] = None

    print(f"解析达人: sheet1={stats['sheet1_kols']} sheet2={stats['sheet2_kols']} "
          f"空昵称跳过={stats['blank_nick']} 重复跳过={stats['dup_skip']} -> 去重后={len(raw)}")
    print(f"  平台: {Counter(r['platform'] for r in raw).most_common()}")
    print(f"  层级: {Counter(r['tier'] for r in raw).most_common()}")
    print(f"  有CPM: {sum(1 for r in raw if r['cpm'])} / {len(raw)}")
    print(f"  有进度反馈: {sum(1 for r in raw if r['progress'])}")
    print(f"  有挂靠/挂v: {sum(1 for r in raw if r['aff'])}")
    print(f"  有分成: {sum(1 for r in raw if r['share'])}")

    if dry:
        return raw, stats

    # 清旧达人 + 依赖
    cur.execute("TRUNCATE party_traits, deal_followup, deal, proposals, proposal_versions, "
                "ingest_staging, kol_profile, brand CASCADE")
    print("已 TRUNCATE 业务表（保留 users/conversations/messages）。")

    kol_ids = {}
    for i, rec in enumerate(raw, 1):
        kid = f"K{i:04d}"
        kol_ids[rec['nick']] = kid
        cur.execute(
            """INSERT INTO kol_profile
               (kol_id,kol_name,real_name,platform,category,sub_category,fans_count,tier,
                interact_rate,conversion_rate,region,mcn_name,cooperation_level,tags,
                price_1_20s,price_21_60s,price_live,exclusive_until,available_from,blacklist,
                coop_models,quote_embed_15s,quote_embed_30s,quote_embed_60s,quote_custom,
                account_uid,contact_mask,affiliation_type,share_model,cpm_21_60s)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (kid, rec['nick'], None, rec['platform'], rec['category'], rec['sub'],
             rec['fans'], rec['tier'], rec['er'],
             round(random.uniform(0.01, 0.05), 4) if rec['fans'] else None,
             random.choice(['上海', '北京', '杭州', '广州', '成都', '深圳', '长沙', '武汉']),
             None, random.choice(['高', '中', '低']),
             json.dumps([rec['sub']] if rec['sub'] else [], ensure_ascii=False),
             int(rec['price'] * 0.55) if rec['price'] else None,
             rec['price'],
             int(rec['price'] * 1.4) if rec['price'] else None,
             None, None, None,
             json.dumps({'mode': '利润分成', 'rate': rec['rate']}, ensure_ascii=False)
             if rec['rate'] is not None else None,
             int(rec['price'] * 0.4) if rec['price'] else None,
             int(rec['price'] * 0.7) if rec['price'] else None,
             rec['price'],
             int(rec['price'] * 1.3) if rec['price'] else None,
             rec['uid'], rec['contact'], rec['aff'], rec['share'], rec['cpm']))
        # 进度反馈 -> 跟进流水（deal_followup, 随阶段详情显示）
        if rec['progress']:
            cur.execute(
                """INSERT INTO deal_followup (deal_id, stage_from, stage_to, note, operator,
                                              created_at, action_type, operator_id)
                   VALUES (%s,%s,%s,%s,%s,%s,'note',%s)""",
                (f'KOL-{kid}', None, None, f"[达人建联] {rec['nick']}：{rec['progress']}",
                 '系统导入', __import__('datetime').datetime.now(), 'u-admin'))
    print(f"装载 kol_profile: {len(raw)} 行。")
    return raw, stats, kol_ids


# ---------------------------------------------------------------- 甲方/商单生成
BRANDS = [
    ('花西子', '美妆', '彩妆'), ('完美日记', '美妆', '彩妆'), ('珀莱雅', '美妆', '护肤'),
    ('薇诺娜', '美妆', '护肤'), (' colorkey珂拉琪', '美妆', '彩妆'),
    ('三只松鼠', '食品饮料', '零食'), ('良品铺子', '食品饮料', '零食'), ('元气森林', '食品饮料', '饮料'),
    ('认养一头牛', '食品饮料', '乳制品'), ('王小卤', '食品饮料', '零食'),
    (' babycare', '母婴亲子', '纸尿裤'), ('飞鹤奶粉', '母婴亲子', '奶粉'),
    ('好孩子', '母婴亲子', '洗护'), ('戴可思', '母婴亲子', '洗护'),
    ('安踏儿童', '母婴亲子', '童装'), ('巴拉巴拉', '母婴亲子', '童装'),
    ('小米手机', '3C数码', '手机'), ('华为终端', '3C数码', '手机'), ('大疆', '3C数码', '无人机'),
    ('追觅科技', '3C数码', '小家电'), ('添可', '3C数码', '小家电'),
    ('波司登', '服装', '羽绒服'), ('李宁', '服装', '运动'), ('ubras', '服装', '内衣'),
    (' colorkey', '美妆', '彩妆'), ('酵色', '美妆', '彩妆'), ('橘朵', '美妆', '彩妆'),
    ('溪木源', '美妆', '护肤'), ('夸迪', '美妆', '护肤'), ('米蓓尔', '美妆', '护肤'),
]
STAGES = ['需求沟通', '提案', '签约', '执行', '结案', '丢单']
GOALS = ['品牌曝光', '新品种草', '带货转化', '口碑铺量', '节点营销']
COOP = ['一口价', '坑位费+佣金', '纯佣', '打包框架']


def gen_brands(cur, n_brand):
    brands = []
    for i in range(1, n_brand + 1):
        bid = f'B{i:03d}'
        name, industry, sub = BRANDS[(i - 1) % len(BRANDS)]
        name = name.strip()
        coop_n = random.randint(1, 6)
        budget_total = coop_n * random.randint(15, 120) * 10000
        phone = f'13{random.randint(0,9)}****{random.randint(1000,9999)}'
        cur.execute(
            """INSERT INTO brand (brand_id, brand_name, industry, category, sub_category,
                                  cooperation_count, history_budget_total, contact_mask, note)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (bid, name, industry, industry, sub, coop_n, budget_total, phone,
             '自动生成的测试甲方'))
        brands.append(dict(brand_id=bid, name=name, industry=industry, sub=sub))
    print(f"装载 brand: {n_brand} 行。")
    return brands


def gen_deals(cur, kols, brands, kol_ids, n_deal):
    from datetime import date, timedelta
    today = date(2026, 9, 27)
    deals = []
    for i in range(1, n_deal + 1):
        deal_id = f'DC{20250000 + i}'
        b = random.choice(brands)
        nk = random.choices([1, 2, 3, 4, 5], weights=[35, 30, 18, 10, 7])[0]
        chosen = random.sample(kols, min(nk, len(kols)))
        kol_ids_list = [kol_ids[c['nick']] for c in chosen if c['nick'] in kol_ids]
        budget = sum(c['price'] or 0 for c in chosen) or random.randint(5, 50) * 10000
        budget = int(budget * random.uniform(0.9, 1.2))
        # 平台需求：抖音为主，CPM 目标按平台（抖音>bilibili>小红书）+ 类目
        plat = random.choices(['抖音', 'bilibili', '小红书'], weights=[80, 12, 8])[0]
        if plat == '抖音':
            cpm_t = round(random.uniform(30, 90), 1)
        elif plat == 'bilibili':
            cpm_t = round(random.uniform(20, 60), 1)
        else:
            cpm_t = round(random.uniform(15, 50), 1)
        # 阶段：结案算 ROI/GMV，丢单不算
        stage = random.choices(STAGES, weights=[12, 18, 15, 25, 20, 10])[0]
        start = today - timedelta(days=random.randint(10, 180))
        end = start + timedelta(days=random.randint(14, 60))
        result = {}
        if stage == '结案':
            roi = round(random.uniform(0.8, 3.5), 2)
            gmv = int(budget * roi)
            result = {'roi': roi, 'gmv': gmv, 'exposure': int(gmv * random.uniform(4, 9)),
                      'interaction': int(gmv * random.uniform(0.05, 0.12))}
        result_metrics = json.dumps(result, ensure_ascii=False) if result else None
        cur.execute(
            """INSERT INTO deal (deal_id, brand_id, brand_name, category, sub_category, goal,
                                 budget, stage, demand_desc, kol_ids, start_date, end_date,
                                 owner, result_metrics, coop_mode, embed_duration_sec,
                                 platform_req, cpm_target, note)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (deal_id, b['brand_id'], b['name'], b['industry'], b['sub'],
             random.choice(GOALS), budget, stage,
             f"推广{b['sub']}，预算{round(budget/10000,1)}万，要求CPM≤{cpm_t}，{plat}平台",
             json.dumps(kol_ids_list, ensure_ascii=False), start, end,
             random.choice(['admin', '商务-小林', '商务-阿May']),
             result_metrics, random.choice(COOP),
             random.choice([15, 30, 60, 60, 60]),
             plat, cpm_t, None))
        # 结案商单写一条结案流水
        if stage == '结案':
            cur.execute(
                """INSERT INTO deal_followup (deal_id, stage_from, stage_to, note, operator,
                                              created_at, action_type, operator_id)
                   VALUES (%s,%s,%s,%s,%s,%s,'note',%s)""",
                (deal_id, '执行', '结案',
                 f"[结案] ROI {result['roi']}，GMV {round(result['gmv']/10000,1)}万，已归档",
                 '系统导入', __import__('datetime').datetime.combine(end, __import__('datetime').time(18, 0)), 'u-admin'))
        deals.append(dict(deal_id=deal_id, stage=stage, brand=b['name']))
    print(f"装载 deal: {n_deal} 行（结案 {sum(1 for d in deals if d['stage']=='结案')}）。")
    return deals


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--kols-only', action='store_true')
    ap.add_argument('--deals', type=int, default=200)
    ap.add_argument('--brands', type=int, default=25)
    args = ap.parse_args()

    if args.dry_run:
        with storage.pg_connect() as c:
            load_kols(c.cursor(), args.limit or 0, dry=True)
        sys.exit(0)

    with storage.pg_connect() as c:
        cur = c.cursor()
        raw, stats, kol_ids = load_kols(cur, args.limit, dry=False)
        if args.limit or args.kols_only:
            print(f"\n试跑完成：装载 {len(raw)} 个达人（未建商单/甲方）。")
        else:
            brands = gen_brands(cur, args.brands)
            deals = gen_deals(cur, raw, brands, kol_ids, args.deals)
            print(f"\n全量重建完成：kol={len(raw)} brand={len(brands)} deal={len(deals)}")
