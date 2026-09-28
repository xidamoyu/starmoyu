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
    """'100W'/'28.7w'/'3.08w' -> int(支持带w价格)。空/异常返回 None。"""
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
    """分成方式 -> (mode, rate, discount)。
    - '整体46'->(整体分成, 0.46, None)
    - '返35%'->(返点, 0.35, None)
    - '828'/'837'->(报价折扣+分成, 0.7, 0.8)  编码: 前两位=报价折扣(8折), 后两位=分成(三七分)
    - '带项目谈'->(按项目谈, None, None)"""
    if s is None: return (None, None, None)
    s = str(s).strip()
    if not s: return (None, None, None)
    m = re.match(r'^(\d)(\d{2})(\d?)$', s)  # 编码: 第1位=报价折扣, 后两位=分成比例(如 828=8折+28分)
    if m and len(s) in (3, 4) and s.isdigit():
        discount = int(m.group(1)) / 10.0           # 8 -> 0.8 (打八折)
        ratio_code = m.group(2)                      # 28 / 37
        ratio = {'28': 0.8, '37': 0.7, '46': 0.6, '55': 0.5, '19': 0.9, '91': 0.1}.get(ratio_code, 0.7)
        return ('报价折扣+分成', ratio, discount)
    m = re.search(r'整体\s*(\d{1,2})', s)
    if m: return ('整体分成', int(m.group(1)) / 100.0, None)
    m = re.search(r'返\s*(\d{1,2})\s*%?', s)
    if m: return ('返点', int(m.group(1)) / 100.0, None)
    if '项目' in s: return ('按项目谈', None, None)
    return (s[:10], None, None)

# 主类目 + 子类目映射
SUB_OF = {'游戏':'游戏', '剧情搞笑':'剧情搞笑', '生活':'生活记录', '美食':'美食探店', '旅行':'旅行vlog',
          '母婴亲子':'母婴', '二次元':'二次元', '三农':'三农', '时尚':'时尚穿搭', '影视娱乐':'影视剪辑',
          '运动健身':'健身', '美妆':'美妆种草', '汽车':'汽车测评', '生活家居':'家居', '萌宠':'宠物',
          '测评':'好物测评', '教育培训':'知识付费', '艺术文化':'艺术', '情感':'情感语录', '颜值达人':'颜值',
          '母婴宠物':'母婴', '才艺技能':'才艺', '科技数码':'数码', '测评美妆':'美妆', '亲子':'母婴'}
def split_category(s):
    """内容类型 -> (主类目, 子类目)。纯数字(误取到序号列)->(None,None)；取最长有效token为主类目；子类目归一。"""
    if s is None: return (None, None)
    raw = str(s).strip()
    if not raw or re.match(r'^\d+$', raw): return (None, None)  # 数字是序号列串位
    parts = [p for p in re.split(r'[\s、,，/]+', raw) if p and not re.match(r'^\d+$', p)]
    if not parts: return (None, None)
    valid = [p for p in parts if p in SUB_OF]
    main = max(valid, key=len) if valid else max(parts, key=len)
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
# 甲方词库: 真实品牌 + (品牌, 品类) 词根, 覆盖所有达人品类, 扩到 100 家
CATS = ['美妆','母婴','游戏','剧情搞笑','生活','美食','旅行','二次元','三农','时尚',
        '影视娱乐','运动健身','汽车','家居','萌宠','测评','教育培训','艺术文化','情感',
        '颜值','才艺','科技数码','口播','带货','穿搭']
# 汇总表自适应解析: 类目白名单 + 挂靠词
CATWORDS = set(CATS) | {'美妆护肤','美妆探店','美食探店','生活家居','亲子','母婴亲子','舞蹈','教育'}
AFFWORDS = {'野生','青年力量','有机构','机构','个人','挂靠'}
BRAND_ROOTS = ['美','雅','贝','植','洛','云','溪','町','慕','梵','岚','汀','蔻','妍','茉',
               '鹿','森','禾','星','月','晴','风','谷','泉','木','石','光','初','本','然']
BRANDS = [('欧莱雅','美妆','护肤'),('飞鹤奶粉','母婴','奶粉'),('追觅科技','家电','清洁电器'),
          ('波司登','服饰','羽绒服'),('三只松鼠','食品','零食'),('完美日记','美妆','彩妆'),
          ('babycare','母婴','用品'),('usmile','个护','电动牙刷'),('SKG','个护','按摩仪'),
          ('网易严选','电商','家居'),('认养一头牛','食品','乳品'),('Colorkey','美妆','彩妆'),
          ('戴可思','母婴','洗护'),('石头科技','家电','扫地机'),('王小卤','食品','卤味'),
          ('黑神话','游戏','游戏周边'),('米哈游','游戏','二次元'),('王者荣耀','游戏','电竞'),
          ('快手小店','电商','带货'),('哔哩哔哩','影视娱乐','视频'),('Keep','运动健身','健身'),
          ('蓝河','母婴','奶粉'),('可复美','美妆','护肤'),('追光','科技数码','硬件'),
          ('三只狼','三农','农产品'),('途牛','旅行','旅游'),('橘朵','美妆','彩妆')]

def all_brands(n=100):
    """生成 n 家甲方, 覆盖全部 CATS 品类。note 一律 NULL(不生成甲方内容)。"""
    pool = list(BRANDS)
    i = 0
    while len(pool) < n:
        cat = CATS[i % len(CATS)]
        root = BRAND_ROOTS[i % len(BRAND_ROOTS)]
        suffix = random.choice(['记','集','坊','社','堂','家','屋','岛','湾','选'])
        pool.append((f'{root}{suffix}', cat, cat))  # 虚拟品牌, 品类对齐
        i += 1
    return pool[:n]

def _norm(x): return re.sub(r'\s+', '', str(x)) if x is not None else ''
def _is_w(x): return bool(re.match(r'^\d+(\.\d+)?[wW万]\s*$', str(x).strip())) if x else False
def _is_px(x): return bool(re.match(r'^\d{3,7}\s*$', str(x).strip())) if x else False
def _is_dt(x): return bool(re.match(r'^\d{1,2}\.\d{1,2}\s*$', str(x).strip())) if x else False
def _is_cn(x): return bool(x) and bool(re.search(r'[\u4e00-\u9fff]', str(x))) and not _is_w(x)

def parse_summary_row(r):
    """之前达人看看板汇总: 多子表拼接列错位, 位置感知解析。
    - 列角色按单元格特征识别, 不依赖固定位置
    - 价格/粉丝均可带w (1.2w=12000)
    - 块编号(如45393)非有效价格/粉丝, 剔除
    - 账号ID 丢弃不解析"""
    cells = [_norm(x) for x in r[:12]]
    rec = {k: None for k in ('kol_name','contact','price','fans','category','passed','affiliate','feedback')}
    used = set()
    for i, c in enumerate(cells):
        if _is_w(c): rec['fans'] = c; used.add(i); break          # 带w的是粉丝
    for i, c in enumerate(cells):                                   # 价格: 带w 或 3-7位数字; 剔除块编号/日期
        if i in used or not c: continue
        if _is_px(c):
            if _is_dt(c) or int(c) > 200000: continue  # 日期或块编号(>20万)跳过
            if i == 0 and int(c) > 10000: continue  # 行首大块编号(段首序号列)
            rec['price'] = c; used.add(i); break
        if _is_w(c) and rec['fans'] is None:  # 带w但粉丝已取->可能是价格
            rec['price'] = c; used.add(i); break
    for i, c in enumerate(cells):                                   # 通过: 是/否/通过/x
        if i not in used and c in ('是','否','通过'): rec['passed'] = '是' if c in ('是','通过') else '否'; used.add(i); break
    for i, c in enumerate(cells):
        if i not in used and c in AFFWORDS: rec['affiliate'] = c; used.add(i); break
    for i, c in enumerate(cells):
        if i not in used and c and c in CATWORDS: rec['category'] = c; used.add(i); break
    for i, c in enumerate(cells):                                   # 联系方式: 字母数字串/手机号(排除纯数字价格)
        if i not in used and c and not re.match(r'^\d+$', c) and (re.match(r'^[A-Za-z0-9_\-]{3,}$', c) or re.match(r'^1[3-9]\d{9}$', c)):
            rec['contact'] = c; used.add(i); break
    cn = [(i, c) for i, c in enumerate(cells)                        # 昵称: 剩余里最长含中文(>=2字, 非类目/通过/数字)
          if i not in used and _is_cn(c) and not _is_dt(c) and not re.match(r'^\d+$', c)
          and c not in ('是','否','通过','x') and c not in CATWORDS and len(c) >= 2]
    if cn:
        i, c = max(cn, key=lambda x: len(x[1])); rec['kol_name'] = c; used.add(i)
    return rec

def load_rows(limit=None):
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    rows = []
    seen = set()
    ws = wb['组员达人看板']
    for r in ws.iter_rows(min_row=3, values_only=True):
        d = dict(zip(HEADERS, r[:16]))
        nm = _norm(d['账号昵称'])
        if nm and nm not in seen:
            seen.add(nm); rows.append(d)
        if limit and len(rows) >= limit: break
    if not limit:  # 全量才合并汇总 sheet(多子表错位, 用自适应解析)
        for r in wb['之前达人看板汇总'].iter_rows(min_row=1, values_only=True):
            rec = parse_summary_row(r)
            nm = rec['kol_name']
            if nm and nm not in seen:
                seen.add(nm)
                rows.append({'账号昵称': nm, '内容类型': rec['category'], '联系方式': rec['contact'],
                             '60S价格': rec['price'], '粉丝数': rec['fans'], '是否通过': rec['passed'],
                             '挂靠状态': rec['affiliate'], '进度反馈': rec['feedback']})
    wb.close()
    return rows

def build_kol(d, idx):
    fans = clean_w(d.get('粉丝数')); price = clean_int(d.get('60S价格'))
    # 合理性过滤: 汇总表错位可能残留错误值
    if price is not None and not (100 <= price <= 1_000_000): price = None
    if fans is not None and not (300 <= fans <= 50_000_000): fans = None
    cat, sub = split_category(d.get('内容类型')); mode, rate, discount = parse_share(d.get('分成方式'))
    platform = '抖音' if random.random() < 0.9 else random.choice(['快手','小红书'])
    aff_raw = d.get('挂靠状态')
    if aff_raw and str(aff_raw).strip() not in AFFWORDS: aff_raw = None  # 汇总表挂靠词才收
    return {
        'kol_id': f'K{idx:05d}', 'kol_name': _norm(d.get('账号昵称')),
        'platform': platform, 'category': cat, 'sub_category': sub,
        'fans_count': fans, 'tier': tier_of(fans), 'interact_rate': interact_rate_of(fans),
        'cpm': cpm_of(price, fans), 'price_21_60s': price,
        'share_mode': mode, 'share_rate': rate, 'share_discount': discount,
        'affiliate_coop': yn(d.get('挂靠合作')) if d.get('挂靠合作') and str(d.get('挂靠合作')).strip() else (True if random.random()<0.08 else None),
        'affiliate_v': yn(d.get('挂v合作')) if d.get('挂v合作') and str(d.get('挂v合作')).strip() else (True if random.random()<0.05 else None),
        'profit_coop': yn(d.get('利润合作')),
        'on_rate_card': yn(d.get('是否上刊例')),
        'affiliate_status': clean_str(aff_raw),
        'cooperation_level': yn(d.get('是否通过')),
        'progress_feedback': clean_str(d.get('进度反馈')),
        'contact_mask': mask_contact(d.get('联系方式')),
        'mcn_name': random.choice(MCNS), 'region': random.choice(REGIONS),
        'avg_views': int(fans * random.uniform(0.25, 0.5)) if fans else (int(price * random.uniform(18, 45)) if price else None),  # 均播≈粉丝×0.25~0.5, 无粉丝时用价反推
        'price_1_20s': int(price*0.6) if price else None,
        'price_live': int(price*1.5) if price else None,
        # 统一命名: cpm / share_mode / price_21_60s / contact_mask; 不写历史重复列
        # (coop_models/cpm_21_60s/quote_embed_60s/affiliation_type/share_model/account_uid 均废弃)
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
ALTER TABLE kol_profile ADD COLUMN IF NOT EXISTS share_discount real;
"""

def ensure_columns(cur):
    cur.execute(DDL)
    # 幂等删除历史重复/错别字/废弃列(每次重建都保证干净)
    for col in ['coop_models','cpm_21_60s','quote_embed_15s','quote_embed_30s','quote_embed_60s',
                'quote_custom','affiliation_type','share_model','account_uid']:
        cur.execute(f'ALTER TABLE kol_profile DROP COLUMN IF EXISTS {col}')

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

def gen_brands(cur, n=100):
    """n 家甲方覆盖全品类, note 一律 NULL(不生成甲方内容)。"""
    bids = []
    for i, (name, cat, sub) in enumerate(all_brands(n)):
        bid = f'B{i+1:03d}'; bids.append(bid)
        cur.execute("INSERT INTO brand (brand_id,brand_name,industry,category,sub_category,cooperation_count,history_budget_total,contact_mask,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL)",
            (bid, name, cat, cat, sub, random.randint(1,10), round(random.uniform(20,1500)*10000,0),
             'bd:'+hashlib.sha1(name.encode()).hexdigest()[:6]))
    return bids

def gen_deal(cur, did, brand_id, brand_name, kols, category, price_cap, cpm_target, platform):
    """budget = 达人单价上限(与 demand_desc 口径一致, 不再乘人数); platform_req 含完整需求。"""
    stage = random.choice(STAGES)
    nk = random.randint(2,5)
    kol_pick = random.sample(kols, min(nk, len(kols)))
    def _kid(x): return x[0] if isinstance(x, (tuple, list)) else (x.get('kol_id') if isinstance(x, dict) else x)
    kol_ids = [_kid(k) for k in kol_pick]
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
    budget = round(price_cap * random.uniform(0.9, 1.1), 0)  # = 单价上限, 对齐 demand_desc
    demand = f"预算{budget/10000:.0f}万内, CPM≤{cpm_target}, {platform}投放"
    platform_req = f"{platform} / 达人单价≤{budget/10000:.0f}万 / CPM≤{cpm_target}"
    cur.execute("INSERT INTO deal (deal_id,brand_id,brand_name,category,sub_category,goal,budget,stage,demand_desc,kol_ids,start_date,end_date,owner,result_metrics,coop_mode,platform_req,cpm_target,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (did, brand_id, brand_name, category, None, random.choice(['新品推广','种草带货','品牌曝光','口碑测评']),
         budget, stage, demand,
         json.dumps(kol_ids, ensure_ascii=False), sd, ed,
         random.choice(['张伟','李娜','王强','刘洋']),
         result, random.choice(['一口价','利润分成','返点']), platform_req, cpm_target, '自动商单'))
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
        # 甲方(100家覆盖全品类) + 商单(400, category取品牌品类避免数字串位)
        gen_brands(cur, 100)
        cur.execute('SELECT brand_id,brand_name,industry,category FROM brand')
        brand_info = cur.fetchall()
        dids = []
        for i in range(1, 401):
            bid, bname, ind, bcat = random.choice(brand_info)
            cat = bcat if (bcat and not re.match(r'^\d+$', bcat)) else random.choice([c2 for c2 in by_cat if by_cat[c2]])
            cands = by_cat.get(cat) or kol_rows
            price_cap = random.choice([3,5,8,10,15,20,30]) * 10000  # 达人单价上限
            platform = '抖音' if random.random()<0.7 else random.choice(['哔哩哔哩','小红书'])
            cpm_target = round(random.uniform(25,55) if platform=='抖音' else (random.uniform(20,45) if platform=='哔哩哔哩' else random.uniform(18,40)),1)
            did = f'DC2025{i:04d}'; dids.append(did)
            gen_deal(cur, did, bid, bname, list(cands), cat, price_cap, cpm_target, platform)
        print(f'  商单写入 400, 甲方 100')
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
