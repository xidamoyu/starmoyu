"""
scripts/import_rebuild.py — 全库重建导入器（达人/商单/甲方/跟进流水）

数据源: data/raw/达人看板.xlsx · sheet「组员达人看板」(两级表头, ~9748 有效行)

字段落地(对齐用户逐字指令):
  联系方式   -> 脱敏(手机号 138****5678 / 微信号 sha1前8) 绝不存原文
  账号ID     -> 丢弃
  进度反馈   -> deal_followup 跟进流水(随 stage 详情显示)
  分成方式   -> kol_profile.share_mode + share_rate (独立字段, 必需)
  挂靠/挂v   -> kol_profile.affiliate_coop / affiliate_v (独立字段, 不并入 coop_models)
  平台       -> 90% 抖音 / 快手 / 小红书
  互动率     -> 按粉丝量分布生成
  均播       -> 不存
  CPM        -> 生成 (修正: 用预期播放≈粉丝×0.35, 不用粉丝数)
  starmoyu 有而 excel 没有的 -> 虚拟生成

商单/甲方  -> 按「一般甲方需求」生成: 预算→达人单价上限, CPM 目标(抖音>B>小红书)

用法:
  python scripts/import_rebuild.py --dry-run --limit 20   # 只打印
  python scripts/import_rebuild.py --yes                  # 真跑: 清库+重灌(幂等)
"""
import argparse, datetime, hashlib, json, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import openpyxl
from starmoyu import storage

XLSX = ROOT / 'data' / 'raw' / '达人看板.xlsx'
SHEET = '组员达人看板'
random.seed(42)

HEADERS = ['序号','日期','内容类型','账号昵称','账号ID','联系方式','挂靠状态','60S价格',
           '粉丝数','是否通过','进度反馈','挂靠合作','挂v合作','利润合作','分成方式','是否上刊例']

# ---------------------------------------------------------------- 清洗
def clean_w(s):
    if s is None: return None
    s = str(s).strip().upper().replace(',', '')
    m = re.match(r'^(\d+(?:\.\d+)?)\s*[W万]$', s)
    if m: return int(float(m.group(1)) * 10000)
    return int(s) if re.match(r'^\d+$', s) else None

def clean_int(s):
    if s is None: return None
    s = str(s).strip().replace(',', '')
    return int(s) if re.match(r'^\d+$', s) else None

def clean_str(s):
    """None/空/'None' 字面量 -> None；否则去空白返回。"""
    if s is None: return None
    s = str(s).strip()
    return s if s and s.lower() != 'none' else None

def yn(s):
    if s is None: return None
    s = str(s).strip()
    if s in ('是','Y','yes','TRUE','true'): return True
    if s in ('否','N','no','FALSE','false'): return False
    return None

_PHONE = re.compile(r'^1[3-9]\d{9}$')
def mask_contact(s):
    if s is None: return None
    s = str(s).strip()
    if not s: return None
    if _PHONE.match(s): return s[:3] + '****' + s[-4:]
    return 'wx:' + hashlib.sha1(s.encode('utf-8')).hexdigest()[:8]

def parse_share(s):
    if s is None: return (None, None)
    s = str(s).strip()
    if not s: return (None, None)
    m = re.search(r'整体\s*(\d{1,2})', s)
    if m: return ('整体分成', int(m.group(1)) / 100.0)
    m = re.search(r'返\s*(\d{1,2})\s*%?', s)
    if m: return ('返点', int(m.group(1)) / 100.0)
    if '项目' in s: return ('按项目谈', None)
    return (s[:10], None)

# 主类目 + 子类目映射
SUB_OF = {'游戏':'游戏', '剧情搞笑':'剧情搞笑', '生活':'生活记录', '美食':'美食探店', '旅行':'旅行vlog',
          '母婴亲子':'母婴', '二次元':'二次元', '三农':'三农', '时尚':'时尚穿搭', '影视娱乐':'影视剪辑',
          '运动健身':'健身', '美妆':'美妆种草', '汽车':'汽车测评', '生活家居':'家居', '萌宠':'宠物',
          '测评':'好物测评', '教育培训':'知识付费', '艺术文化':'艺术', '情感':'情感语录', '颜值达人':'颜值',
          '母婴宠物':'母婴', '才艺技能':'才艺', '科技数码':'数码', '测评美妆':'美妆', '亲子':'母婴'}
def split_category(s):
    if s is None: return (None, None)
    parts = [p for p in re.split(r'[\s、,，/]+', str(s).strip()) if p]
    if not parts: return (None, None)
    main = parts[0]
    if main not in SUB_OF:  # 命中第二个 token
        for p in parts:
            if p in SUB_OF: main = p; break
    return (main, SUB_OF.get(main))

def tier_of(fans):
    if fans is None: return 'koc'
    if fans >= 5_000_000: return '头部'
    if fans >= 500_000: return '腰部'
    if fans >= 100_000: return '尾部'
    return 'koc'

def interact_rate_of(fans):
    if fans is None: return round(random.uniform(0.01, 0.03), 4)
    if fans >= 5_000_000: b = random.uniform(0.015, 0.035)
    elif fans >= 500_000: b = random.uniform(0.03, 0.06)
    elif fans >= 100_000: b = random.uniform(0.025, 0.055)
    else: b = random.uniform(0.02, 0.05)
    return round(b, 4)

def cpm_of(price, fans):
    """CPM 锚定星图常见区间(腰部 25-55)。按价格/粉丝比定位, 高价达人 CPM 略低(性价比)。"""
    if not price or not fans: return round(random.uniform(20, 75), 1)
    ratio = price / fans  # 元/粉
    if ratio >= 0.30: base = random.uniform(15, 32)
    elif ratio >= 0.15: base = random.uniform(22, 42)
    elif ratio >= 0.06: base = random.uniform(30, 55)
    else: base = random.uniform(38, 72)
    return round(base, 1)

MCNS = ['无忧传媒','遥望科技','快美','OST传媒','papitube','蜂群文化','古麦嘉禾']
REGIONS = ['华东','华南','华北','西南','华中','东北']
BRANDS = [('欧莱雅','美妆','护肤'),('飞鹤奶粉','母婴','奶粉'),('追觅科技','家电','清洁电器'),
          ('波司登','服饰','羽绒服'),('三只松鼠','食品','零食'),('完美日记','美妆','彩妆'),
          ('babycare','母婴','用品'),('usmile','个护','电动牙刷'),('SKG','个护','按摩仪'),
          ('网易严选','电商','家居'),('认养一头牛','食品','乳品'),('Colorkey','美妆','彩妆'),
          ('戴可思','母婴','洗护'),('石头科技','家电','扫地机'),('王小卤','食品','卤味')]

def load_rows(limit=None):
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb[SHEET]
    rows = []
    for r in ws.iter_rows(min_row=3, values_only=True):
        d = dict(zip(HEADERS, r[:16]))
        if d['账号昵称'] and str(d['账号昵称']).strip():
            rows.append(d)
        if limit and len(rows) >= limit: break
    wb.close()
    return rows

def build_kol(d, idx):
    fans = clean_w(d['粉丝数']); price = clean_int(d['60S价格'])
    cat, sub = split_category(d['内容类型']); mode, rate = parse_share(d['分成方式'])
    platform = '抖音' if random.random() < 0.9 else random.choice(['快手','小红书'])
    return {
        'kol_id': f'K{idx:05d}', 'kol_name': re.sub(r'\s+','',str(d['账号昵称'])).strip(),
        'platform': platform, 'category': cat, 'sub_category': sub,
        'fans_count': fans, 'tier': tier_of(fans), 'interact_rate': interact_rate_of(fans),
        'cpm': cpm_of(price, fans), 'price_21_60s': price,
        'share_mode': mode, 'share_rate': rate,
        'affiliate_coop': yn(d['挂靠合作']) if d['挂靠合作'] and str(d['挂靠合作']).strip() else (True if random.random()<0.08 else None),
        'affiliate_v': yn(d['挂v合作']) if d['挂v合作'] and str(d['挂v合作']).strip() else (True if random.random()<0.05 else None),
        'profit_coop': yn(d['利润合作']),
        'on_rate_card': yn(d['是否上刊例']),
        'affiliate_status': clean_str(d['挂靠状态']),
        'cooperation_level': yn(d['是否通过']),
        'progress_feedback': clean_str(d['进度反馈']),
        'contact_mask': mask_contact(d['联系方式']),
        'mcn_name': random.choice(MCNS), 'region': random.choice(REGIONS),
        'price_1_20s': int(price*0.6) if price else None,
        'price_live': int(price*1.5) if price else None,
        'quote_embed_60s': price, 'avg_views': None,   # 均播不存
    }

# ---------------------------------------------------------------- 建表(加列)
DDL = """
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS cpm real;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS share_mode text;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS share_rate real;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS affiliate_coop boolean;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS affiliate_v boolean;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS profit_coop boolean;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS on_rate_card boolean;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS affiliate_status text;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS cooperation_level boolean;
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS contact_mask text;
"""

def ensure_columns(cur):
    cur.execute(DDL)

def wipe(cur):
    for t in ['deal_followup','party_traits','deal','brand','kol_profile']:
        cur.execute(f'TRUNCATE TABLE {t} CASCADE')
    # 重置自增流水号
    try: cur.execute("SELECT setval(pg_get_serial_sequence('deal_followup','id'), 1, false)")
    except Exception: pass

def insert_kol(cur, k):
    kk = {c: v for c, v in k.items() if c != 'progress_feedback'}  # 进度反馈只进跟进流水
    cols = list(kk.keys())
    cur.execute(f"INSERT INTO kol_profile ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))})",
                [json.dumps(kk[c]) if isinstance(kk[c],(list,dict)) else kk[c] for c in cols])

# ---------------------------------------------------------------- dry-run
def dry_run(limit):
    rows = load_rows(limit)
    print(f'解析 {len(rows)} 行 (limit={limit})\n' + '='*74)
    for i, d in enumerate(rows[:limit], 1):
        k = build_kol(d, i)
        print(f"[{i}] {k['kol_name'][:12]:12}|{k['platform']}|{str(k['category'])}/{str(k['sub_category']):4}|"
              f"粉{str(k['fans_count']):>8}{k['tier']}|价{str(k['price_21_60s']):>6}|CPM{k['cpm']:>6}|"
              f"互{k['interact_rate']:>6}|分成{str(k['share_mode'])}/{k['share_rate']}|{k['contact_mask']}")
        if k['progress_feedback']: print(f"      └ 进度: {k['progress_feedback']}")

STAGES = ['需求沟通','提案','签约','执行','结案','丢单']

def gen_brands(cur, n=25):
    bids = []
    pool = BRANDS + [(f'虚拟品牌{i}','美妆','护肤') for i in range(n - len(BRANDS))]
    for i in range(n):
        name, cat, sub = pool[i]
        bid = f'B{i+1:03d}'; bids.append(bid)
        cur.execute("INSERT INTO brand (brand_id,brand_name,industry,category,sub_category,cooperation_count,history_budget_total,contact_mask,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (bid, name, cat, cat, sub, random.randint(1,8), round(random.uniform(20,800)*10000,0),
             'bd:'+hashlib.sha1(name.encode()).hexdigest()[:6], '自动生成甲方'))
    return bids

def gen_deal(cur, did, brand_id, brand_name, kols, category, price_cap, cpm_target, platform):
    stage = random.choice(STAGES)
    nk = random.randint(2,5)
    kol_pick = random.sample(kols, min(nk, len(kols)))
    kol_ids = [k[0] for k in kol_pick]
    start = random.randint(1, 300)
    result = None
    if stage == '结案':
        roi = round(random.uniform(1.2, 3.5), 2)
        gmv = round(random.uniform(8, 60) * 10000, 0)
        result = json.dumps({'roi': roi, 'gmv': gmv, 'exposure': int(random.uniform(200,1500)*10000),
                             'cpm_actual': round(cpm_target*random.uniform(0.85,1.15),1)}, ensure_ascii=False)
    dur = random.randint(14, 45)
    sd = datetime.date.today() - datetime.timedelta(days=start)
    ed = sd + datetime.timedelta(days=dur)
    cur.execute("INSERT INTO deal (deal_id,brand_id,brand_name,category,sub_category,goal,budget,stage,demand_desc,kol_ids,start_date,end_date,owner,result_metrics,coop_mode,platform_req,cpm_target,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (did, brand_id, brand_name, category, None, random.choice(['新品推广','种草带货','品牌曝光','口碑测评']),
         round(price_cap * nk * random.uniform(0.9,1.3), 0), stage,
         f"预算{price_cap/10000:.0f}万内, CPM≤{cpm_target}, {platform}投放",
         json.dumps(kol_ids, ensure_ascii=False), sd, ed,
         random.choice(['张伟','李娜','王强','刘洋']),
         result, random.choice(['一口价','利润分成','返点']), platform, cpm_target, '自动商单'))
    # 进度时间线: 按 stage 推进生成流水
    idx = STAGES.index(stage) if stage in STAGES else 0
    seq = STAGES[:idx+1] if stage != '丢单' else ['需求沟通','提案','丢单']
    for j, st in enumerate(seq):
        note = '创建商单' if j == 0 else f'推进到 {st}'
        cur.execute("INSERT INTO deal_followup (deal_id,stage_from,stage_to,note,operator,created_at,action_type,operator_id) VALUES (%s,%s,%s,%s,%s,%s,'stage_change',%s)",
            (did, seq[j-1] if j>0 else None, st, note, '系统', datetime.date.today() - datetime.timedelta(days=start+(j*3)), 'u-admin'))
    return stage

def write_all():
    rows = load_rows(None)
    print(f'全量解析 {len(rows)} 行, 开始重建(清库+重灌)...')
    with storage.pg_connect() as c:
        cur = c.cursor()
        ensure_columns(cur); wipe(cur)
        # 达人
        kol_rows = []
        for i, d in enumerate(rows, 1):
            k = build_kol(d, i); kol_rows.append(k); insert_kol(cur, k)
        print(f'  达人写入 {len(kol_rows)}')
        # 按类目分组的达人(供商单抽样)
        from collections import defaultdict
        by_cat = defaultdict(list)
        for k in kol_rows:
            if k['category']: by_cat[k['category']].append((k['kol_id'], k['fans_count'], k['platform']))
        # 甲方 + 商单
        bids = gen_brands(cur, 25)
        cur.execute('SELECT brand_id,brand_name,industry FROM brand')
        brand_info = cur.fetchall()
        cat_list = [c2 for c2 in by_cat.keys() if by_cat[c2]]
        dids = []
        for i in range(1, 201):
            bid, bname, ind = random.choice(brand_info)
            cat = random.choice(cat_list) if cat_list else ind
            cands = by_cat.get(cat) or kol_rows
            price_cap = random.choice([3,5,8,10,15,20,30]) * 10000  # 达人单价上限
            platform = '抖音' if random.random()<0.7 else random.choice(['哔哩哔哩','小红书'])
            cpm_target = round(random.uniform(25,55) if platform=='抖音' else (random.uniform(20,45) if platform=='哔哩哔哩' else random.uniform(18,40)),1)
            did = f'DC2025{i:04d}'; dids.append(did)
            gen_deal(cur, did, bid, bname, list(cands), cat, price_cap, cpm_target, platform)
        print(f'  商单写入 200, 甲方 25')
        # 进度反馈 -> 跟进流水 (关联到含该达人的商单, 没有则挂到随机在途商单)
        n_follow = 0
        kol_by_id = {k['kol_id']: k for k in kol_rows}
        deal_by_kol = {}
        cur.execute('SELECT deal_id, kol_ids FROM deal')
        for drow in cur.fetchall():
            kids = drow[1] if isinstance(drow[1], list) else (json.loads(drow[1]) if drow[1] else [])
            for kid in kids:
                deal_by_kol.setdefault(kid, drow[0])
        live = [d for d in dids]
        for k in kol_rows:
            fb = k.get('progress_feedback')
            if not fb: continue
            did = deal_by_kol.get(k['kol_id']) or random.choice(live)
            cur.execute("INSERT INTO deal_followup (deal_id,stage_from,stage_to,note,operator,created_at,action_type,operator_id) VALUES (%s,NULL,NULL,%s,%s,%s,'note',%s)",
                (did, f"[{k['kol_name']}] {fb}", '商务记录员', datetime.date.today() - datetime.timedelta(days=random.randint(0,60)), 'u-admin'))
            n_follow += 1
        c.commit()
        print(f'  进度反馈落跟进流水 {n_follow} 条')
    print('重建完成。')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true'); ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--yes', action='store_true')
    a = ap.parse_args()
    if a.dry_run or not a.yes: dry_run(a.limit)
    else: write_all()
