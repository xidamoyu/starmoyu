"""W1 数据构造：生成一套自洽的 MCN 仿真数据资产。

产出：
  data/synthetic/kol_profile.json    达人档案（120 条）
  data/synthetic/brand.json          广告主（40 条）
  data/synthetic/deal.json           商单台账（90 条，含在途）
  data/synthetic/rate_card.json      刊例（按达人）
  data/synthetic/deal_followup.json  跟进流水
  data/raw/**/*.md                   可检索语料（平台规则/刊例/商单案例/方法论）

设计要点：所有文档内容都从结构化数据渲染而来 —— 保证「引用溯源」可验证，
不会出现文档与台账对不上的假数据。
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "synthetic"
RAW = ROOT / "data" / "raw"
SEED = 20260924
rng = random.Random(SEED)

# ---------------------------------------------------------------- 基础字典

CATEGORIES = {
    "美妆": ["精华护肤", "彩妆", "防晒", "洗护", "香水"],
    "母婴": ["奶粉辅食", "纸尿裤", "童装童鞋", "玩具早教", "孕产护理"],
    "3C数码": ["手机", "耳机音频", "智能穿戴", "笔记本平板", "摄影器材"],
    "食品饮料": ["休闲零食", "冲饮咖啡", "乳制品", "方便速食", "健康保健"],
    "服饰": ["女装", "男装", "运动户外", "内衣", "鞋靴箱包"],
    "家居": ["家纺", "清洁用品", "厨房小电", "收纳整理", "家具"],
    "汽车": ["新能源整车", "汽车用品", "轮胎养护", "车险服务"],
    "游戏": ["手游推广", "端游推广", "游戏外设"],
}

PLATFORMS = ["抖音", "小红书", "快手"]
PLATFORM_W = [0.62, 0.28, 0.10]

TIERS = [("头部", 500, 2600), ("腰部", 50, 500), ("尾部", 5, 50)]

KOL_TAGS = {
    "美妆": ["成分党", "测评向", "素人改造", "妆教", "空瓶记录", "性价比推荐", "专业配方师"],
    "母婴": ["育儿知识", "萌娃日常", "辅食教程", "母婴测评", "宝妈vlog", "囤货清单"],
    "3C数码": ["参数党", "开箱测评", "硬核拆解", "科技资讯", "数码好物", "体验分享"],
    "食品饮料": ["探店", "美食制作", "零食试吃", "健康饮食", "囤货分享", "口感测评"],
    "服饰": ["穿搭教程", "OOTD", "大码穿搭", "通勤穿搭", "小众设计", "面料科普"],
    "家居": ["收纳达人", "改造翻新", "清洁技巧", "家居好物", "小户型优化"],
    "汽车": ["试驾评测", "用车技巧", "新能源科普", "改装", "车主vlog"],
    "游戏": ["攻略解说", "高光时刻", "版本前瞻", "设备测评", "赛事解说"],
}
GOALS = ["带货", "种草", "曝光", "新品发布"]
STAGES = ["需求沟通", "提案", "签约", "执行", "结案", "丢单"]
STAGE_W = [0.12, 0.16, 0.12, 0.18, 0.34, 0.08]
OWNERS = ["运营-林岚", "运营-周琦", "运营-陈默", "商务-苏夏", "商务-郑野"]
BRAND_INDUSTRY = ["美妆个护", "母婴亲子", "3C数码", "食品饮料", "服饰鞋包", "家居家电", "汽车出行", "游戏互娱"]

CN_SURNAME = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜")
CN_GIVEN = ["小满", "亦然", "星野", "知夏", "南乔", "沐言", "与安", "叙白", "清和", "锦书",
            "望舒", "灵均", "澜依", "时予", "晏宁", "稚川", "昭昭", "皎皎", "悠悠", "砚青"]
HANDLE_PREFIX = ["", "是", "一只", "爱", "阿", "小", "老"]
HANDLE_SUFFIX = ["酱", "君", "记", "研究所", "实验室", "日记", "笔记", "说", "铺子", "日常"]


def cn_name(seed_i: int) -> str:
    r = random.Random(SEED + seed_i * 7919)
    return r.choice(CN_SURNAME) + r.choice(CN_GIVEN)


def handle(seed_i: int, category: str) -> str:
    r = random.Random(SEED + seed_i * 104729)
    cat_key = {"美妆": "美妆", "母婴": "育儿", "3C数码": "数码", "食品饮料": "美食",
               "服饰": "穿搭", "家居": "家home", "汽车": "车", "游戏": "游戏"}
    key = cat_key.get(category, "好物")
    key = {"家home": "家居"}.get(key, key)
    return f"{r.choice(HANDLE_PREFIX)}{key}{r.choice(HANDLE_SUFFIX)}"


# ---------------------------------------------------------------- 生成达人

def gen_kols(n: int = 120) -> list[dict]:
    kols = []
    cats = list(CATEGORIES.keys())
    for i in range(n):
        cat = cats[i % len(cats)]
        sub = rng.choice(CATEGORIES[cat])
        platform = rng.choices(PLATFORMS, weights=PLATFORM_W)[0]
        tier, lo, hi = rng.choices(TIERS, weights=[0.12, 0.5, 0.38])[0]
        fans = int(rng.uniform(lo, hi) * (10_000 if tier != "头部" else 10_000))
        if tier == "头部":
            fans = rng.randint(500_0000, 2600_0000)
        elif tier == "腰部":
            fans = rng.randint(50_0000, 500_0000)
        else:
            fans = rng.randint(5_0000, 50_0000)
        interact = round(rng.uniform(0.008, 0.062) * (1.18 if tier == "尾部" else 1.0), 4)
        conv = round(rng.uniform(0.004, 0.031) * (1.25 if tier == "尾部" else 1.0), 4)
        avg_views = int(fans * rng.uniform(0.06, 0.42))
        female = round(rng.uniform(0.25, 0.92) if cat in ("美妆", "母婴", "服饰", "家居") else rng.uniform(0.15, 0.65), 2)
        age = rng.choice([
            {"18-23": 0.34, "24-30": 0.41, "31-40": 0.19, "40+": 0.06},
            {"18-23": 0.18, "24-30": 0.38, "31-40": 0.31, "40+": 0.13},
            {"18-23": 0.41, "24-30": 0.37, "31-40": 0.17, "40+": 0.05},
        ])
        # 报价：与粉丝量、量级、互动率挂钩（保证刊例合理）
        base = fans / 10000 * rng.uniform(120, 260)
        if tier == "头部":
            base *= 1.25
        p1 = int(round(base * 0.55, -2))
        p2 = int(round(base, -2))
        plive = int(round(base * rng.uniform(1.3, 2.6), -2))
        kols.append({
            "kol_id": f"K{i+1:04d}",
            "kol_name": handle(i, cat),
            "real_name": cn_name(i),
            "platform": platform,
            "category": cat,
            "sub_category": sub,
            "fans_count": fans,
            "tier": tier,
            "avg_views": avg_views,
            "interact_rate": interact,
            "conversion_rate": conv,
            "gender_ratio": {"female": female, "male": round(1 - female, 2)},
            "age_ratio": age,
            "region": rng.choice(["北京", "上海", "广州", "深圳", "杭州", "成都", "长沙", "武汉"]),
            "mcn_name": rng.choice(["本司直签", "本司直签", "本司直签", "合作MCN-星合", "合作MCN-云雀"]),
            "cooperation_level": rng.choices(["S", "A", "B", "C"], weights=[0.12, 0.33, 0.37, 0.18])[0],
            "tags": rng.sample(KOL_TAGS[cat], k=rng.randint(2, 4)),
            "price_1_20s": p1,
            "price_21_60s": p2,
            "price_live": plive,
        })
    return kols


def gen_brands(n: int = 40) -> list[dict]:
    """品牌名与类目严格对齐，避免出现「罗纹内衣-美妆」这类错位。"""
    name_pool = {
        "美妆": ["肌研研究所", "LANZUO蓝佐", "轻氧CLEAIR", "沐光护肤", "采薇彩妆", "倍滋养"],
        "母婴": ["小象树", "奶糖星球", "米兔童装", "云朵母婴", "初见童鞋", "贝知味"],
        "3C数码": ["声take声", "极影视界", "轻联数码", "声浪音频", "雷神外设", "砚川科技"],
        "食品饮料": ["元气发酵", "三顿半刻", "牛気乳业", "速食星球", "果小茶", "鲜物语"],
        "服饰": ["素冉", "行野户外", "罗纹内衣", "风行鞋业", "简棉服饰", "织野"],
        "家居": ["织眠家纺", "净博士", "小厨匠", "收好物", "木野家具", "拾光家清"],
        "汽车": ["星驰新能源", "驰加养护", "环球轮胎", "途安车品", "启程出行"],
        "游戏": ["掌趣互娱", "神州手游", "山海经手游", "云顶互娱", "疾风游戏"],
    }
    brands, used = [], {}
    cats = list(CATEGORIES.keys())
    for i in range(n):
        cat = cats[i % len(cats)]
        idx = used.get(cat, 0)
        pool = name_pool[cat]
        brand_name = pool[idx % len(pool)] + ("" if idx < len(pool) else f"·{idx//len(pool)+1}期")
        used[cat] = idx + 1
        brands.append({
            "brand_id": f"B{i+1:03d}",
            "brand_name": brand_name,
            "industry": BRAND_INDUSTRY[cats.index(cat) % len(BRAND_INDUSTRY)],
            "category": cat,
            "sub_category": rng.choice(CATEGORIES[cat]),
            "cooperation_count": rng.randint(1, 26),
            "history_budget_total": round(rng.uniform(30, 900) * 10000, 2),
        })
    return brands


# ---------------------------------------------------------------- 生成商单

def pick_kols(kols: list[dict], category: str, k: int, tier: str | None = None) -> list[dict]:
    pool = [x for x in kols if x["category"] == category and (tier is None or x["tier"] == tier)]
    if len(pool) < k:
        pool = [x for x in kols if x["category"] == category]
    if len(pool) < k:
        pool = kols
    return rng.sample(pool, k=k)


def gen_deals(kols: list[dict], brands: list[dict], n: int = 90) -> list[dict]:
    deals = []
    today = date(2026, 9, 24)
    for i in range(n):
        brand = rng.choice(brands)
        cat = brand["category"]
        tier_pref = rng.choices(["头部", "腰部", "尾部", None], weights=[0.15, 0.5, 0.25, 0.10])[0]
        kcount = rng.randint(1, 6)
        chosen = pick_kols(kols, cat, kcount, tier_pref)
        # 需求描述里的量级用实际选中达人的真实分布，避免文实不符
        real_tiers = sorted({x["tier"] for x in chosen}, key=lambda t: ["头部", "腰部", "尾部"].index(t))
        tier_text = "/".join(real_tiers) + "达人"
        platform_text = "/".join(sorted({x["platform"] for x in chosen}))
        budget = float(sum(x["price_21_60s"] for x in chosen) * rng.uniform(0.9, 1.3))
        budget = float(round(round(budget, -3), 2))
        stage = rng.choices(STAGES, weights=STAGE_W)[0]
        deal_date = today - timedelta(days=rng.randint(5, 400))
        if stage in ("需求沟通", "提案", "签约", "执行"):
            updated = today - timedelta(days=rng.randint(0, 12))
            end_date = today + timedelta(days=rng.randint(3, 40))
            metrics = None
        elif stage == "结案":
            updated = deal_date + timedelta(days=rng.randint(14, 50))
            end_date = updated
            # ROI 以预算为锚，生成符合行业区间的分布（多数微利，少数爆单或失手）
            roi = round(rng.lognormvariate(0.12, 0.42), 2)
            roi = min(max(roi, 0.35), 3.6)
            gmv = round(budget * roi, 2)
            # CPM 按行业 25-60 元反推曝光量，保证数据自洽
            cpm = rng.uniform(25, 60)
            reach = int(budget / cpm * 1000)
            metrics = {
                "exposure": reach,
                "interaction": int(reach * rng.uniform(0.012, 0.055)),
                "gmv": gmv,
                "roi": roi,
                "cpm": round(cpm, 2),
            }
        else:  # 丢单
            updated = deal_date + timedelta(days=rng.randint(3, 20))
            end_date = updated
            metrics = None
        goal = rng.choice(GOALS)
        female_first = rng.random() < 0.45
        audience = f"{'女性向' if female_first else '中性向'} · 18-30 岁为主 · 一二线城市" if rng.random() < 0.7 else "全人群 · 下沉市场占比较高"
        deals.append({
            "deal_id": f"DC{deal_date.year}{i+1:04d}",
            "brand_id": brand["brand_id"],
            "brand_name": brand["brand_name"],
            "category": cat,
            "sub_category": brand["sub_category"],
            "goal": goal,
            "budget": budget,
            "stage": stage,
            "demand_desc": (
                f"{brand['brand_name']}计划在{platform_text}投放{cat}类目产品，"
                f"目标是{goal}，预算 {budget/10000:.1f} 万，希望合作 {kcount} 位"
                f"{tier_text}，受众{audience}。"
            ),
            "kol_ids": [x["kol_id"] for x in chosen],
            "start_date": deal_date.isoformat(),
            "end_date": end_date.isoformat(),
            "owner": rng.choice(OWNERS),
            "result_metrics": metrics,
            "created_at": f"{deal_date.isoformat()} 10:{rng.randint(10,59):02d}:00",
            "updated_at": f"{updated.isoformat()} 1{rng.randint(0,7)}:{rng.randint(10,59):02d}:00",
            "_updated_date": updated,
            "_schedule_note": rng.choice(["需在大促前完成上线", "配合新品发布会节奏", "档期较紧，需 2 周内执行", "可接受 1 个月长周期", "需避开竞品投放窗口"]),
        })
    return deals


def gen_followups(deals: list[dict]) -> list[dict]:
    rows, rid = [], 1
    for d in deals:
        steps = rng.randint(1, 4)
        cur = datetime.fromisoformat(d["created_at"])
        path = ["需求沟通"]
        for s in STAGES[1:5]:
            if STAGES.index(s) <= STAGES.index(d["stage"]):
                path.append(s)
        if d["stage"] == "丢单":
            path.append("丢单")
        for j, st in enumerate(path[1:steps + 1]):
            cur += timedelta(days=rng.randint(1, 6))
            rows.append({
                "id": rid,
                "deal_id": d["deal_id"],
                "stage_from": path[j],
                "stage_to": st,
                "note": rng.choice([
                    "广告主确认需求，等待方案", "已发送提案，等待反馈", "报价谈判中，客户要求下调 8%",
                    "合同已签署，走内部排期", "达人已确认档期，内容脚本已过审", "内容已上线，数据爬取中",
                    "结案报告已交付，回款流程中", "客户预算调整，商单暂缓",
                ]),
                "operator": d["owner"],
                "created_at": cur.isoformat(sep=" "),
            })
            rid += 1
            if st in ("结案", "丢单"):
                break
    return rows


# ---------------------------------------------------------------- 渲染语料

def render_platform_rules() -> list[tuple[str, str, str]]:
    """(相对路径, 标题, 正文) —— 平台规则/SOP 类文档"""
    docs = []
    rules = [
        ("抖音星图下单与结算规则.md", "抖音星图下单与结算规则", [
            ("## 一、下单入口与流程", [
                "广告主需在巨量星图平台完成企业认证后，方可发起达人合作任务。",
                "下单流程为：需求发布 → 达人报名/定向邀约 → 双向确认 → 生成订单 → 支付保证金 → 内容创作 → 提交审核 → 发布 → 数据回传 → 结算。",
                "定向邀约模式下，单次最多可邀约 20 位达人；公开任务模式下无邀约数量限制。",
                "订单创建后，达人须在 48 小时内确认接单，逾期订单自动取消。",
            ]),
            ("## 二、报价与费用构成", [
                "达人报价由达人自行设置，区分 1-20 秒短视频、21-60 秒短视频、直播专场三类刊例。",
                "平台服务费按订单金额的 5% 向广告主收取，另按 10% 向达人侧收取（具体以当期政策为准）。",
                "报价调整需提前 7 个自然日生效，已生成订单不受调价影响。",
                "定制内容（如剧情植入、多平台分发）通常在标准刊例基础上上浮 20%-50%。",
            ]),
            ("## 三、内容审核与修改", [
                "脚本需提前 3 个工作日提交审核，平台审核通常 1-2 个工作日返回。",
                "内容修改次数上限为 3 次，超出部分需双方另行协商。",
                "涉及医疗、保健、金融品类的投放，必须提供对应资质文件，否则不予过审。",
                "禁用绝对化用语（如“最”“第一”“国家级”），违者内容下架并计入违规。",
            ]),
            ("## 四、数据回传与结案", [
                "内容发布后 24 小时、7 天、30 天三个节点自动回传数据。",
                "结案报告应包含曝光量、互动量、互动率、CPM、进店/转化数据。",
                "若内容因达人原因下架，订单需重新协商或退款处理。",
            ]),
        ]),
        ("巨量星图达人合作常见问题.md", "巨量星图达人合作常见问题", [
            ("## 达人如何分级", [
                "行业惯例按粉丝量分为头部（500 万以上）、腰部（50 万-500 万）、尾部（50 万以下）。",
                "本司内部另设 S/A/B/C 合作评级，综合考量履约率、内容质量、配合度与历史效果。",
                "S 级达人可优先获得档期，并享受寄样与脚本共创支持。",
            ]),
            ("## 报价怎么谈", [
                "首次合作报价通常为刊例的 85%-100%；复购合作可谈至刊例的 70%-85%。",
                "打包多位达人（3 位以上）通常可争取 10%-20% 的整体折扣。",
                "长周期框架合作（季度及以上）可谈固定折扣 + 保底曝光条款。",
                "纯佣金模式下，达人分成比例一般为主流行业 GMV 的 15%-30%。",
            ]),
            ("## 常见风险", [
                "档期冲突：头部达人档期通常需提前 20-30 天锁定。",
                "数据注水：需关注互动率异常（如互动率显著高于同量级均值）与粉丝画像突变。",
                "合规风险：美妆护肤品不得承诺功效，母婴类目不得宣称“替代母乳”。",
                "结算风险：广告主逾期付款将影响后续合作评分，需在合同中约定回款节点。",
            ]),
        ]),
        ("商单立项与执行SOP.md", "商单立项与执行 SOP", [
            ("## 一、立项标准", [
                "预算 ≥ 10 万元或潜在年框客户，方可立项并由商务负责人指派主运营。",
                "立项需产出：需求确认单、达人推荐清单、预算分配表、执行排期表。",
                "预算分配建议：达人费用占 75%-85%，内容制作 5%-10%，机动预算 5%-15%。",
            ]),
            ("## 二、达人匹配原则", [
                "第一优先：类目匹配度 —— 达人主类目与产品类目一致，历史同类目商单效果可查。",
                "第二优先：人群重合度 —— 达人粉丝画像与目标受众性别、年龄重合度 ≥ 60%。",
                "第三优先：性价比 —— 在预算内优先选择互动率与转化率高于同量级均值的达人。",
                "第四优先：履约稳定性 —— 优先选择 A 级以上、历史无延期记录的达人。",
            ]),
            ("## 三、执行节奏（30 天标准周期）", [
                "D1-D3：需求确认与达人初筛，输出候选清单。",
                "D4-D7：报价谈判与档期锁定，签署合同。",
                "D8-D12：脚本撰写与审核，寄样。",
                "D13-D18：内容拍摄与制作。",
                "D19-D21：内容审核与修改。",
                "D22-D28：集中发布与数据监控。",
                "D29-D30：复盘与结案报告交付。",
            ]),
            ("## 四、结案归档要求", [
                "结案后 5 个工作日内完成归档，包含结案报告、数据截图、达人评价。",
                "归档数据将进入公司商单资产库，供后续项目复用。",
                "ROI 低于 1.0 的商单需在结案报告中说明原因与改进项。",
            ]),
        ]),
    ]
    for fname, title, sections in rules:
        body = [f"# {title}", ""]
        for h, paras in sections:
            body.append(h)
            body.append("")
            for p in paras:
                body.append(f"- {p}")
            body.append("")
        docs.append((f"policies/{fname}", title, "\n".join(body)))
    return docs


def render_playbook() -> list[tuple[str, str, str]]:
    docs = []
    content = [
        ("美妆类目投放方法论.md", "美妆类目投放方法论", [
            ("## 达人组合策略", [
                "美妆投放推荐“1 头部 + 3-5 腰部 + 5-10 尾部”的漏斗式组合：头部建信任、腰部做转化、尾部铺口碑。",
                "精华、防晒等高客单产品，优先选择成分党、配方师类达人，转化率通常高于泛美妆达人 30% 以上。",
                "彩妆类目适合妆教、素人改造类达人，内容可复制性强，尾部达人性价比高。",
            ]),
            ("## 内容要点", [
                "必须包含产品核心成分或卖点，避免纯口播硬广。",
                "建议采用“痛点场景 → 产品介入 → 使用前后对比”的三段式结构。",
                "合规红线：不得使用“美白”“祛斑”等功效宣称，可用“提亮”“改善暗沉”等表述。",
            ]),
            ("## 效果基准（行业参考）", [
                "美妆类目 CPM 参考区间 25-60 元；互动率参考 2%-5%。",
                "腰部达人带货 ROI 基准 ≥ 1.2；头部达人因品牌曝光溢价，ROI 基准可放宽至 0.8。",
            ]),
        ]),
        ("达人筛选评估框架.md", "达人筛选评估框架", [
            ("## 五维评估模型", [
                "类目匹配度（权重 25%）：主类目、历史商单类目分布。",
                "人群重合度（权重 25%）：粉丝性别、年龄、地域与目标受众的重合比例。",
                "内容质量（权重 20%）：完播率、互动率、内容调性一致性。",
                "性价比（权重 20%）：CPM、CPE 与同量级均值对比。",
                "履约稳定性（权重 10%）：历史交付准时率、改稿配合度。",
            ]),
            ("## 硬性排除项", [
                "近 90 天存在刷量违规记录的达人，一票否决。",
                "粉丝画像突变（近 30 天女性占比波动 > 25%）的账号需人工复核。",
                "互动率低于同量级均值 50% 的达人，原则上不进入候选池。",
            ]),
        ]),
        ("提案与报价话术要点.md", "提案与报价话术要点", [
            ("## 提案结构", [
                "标准提案包含：需求理解 → 达人组合 → 内容创意 → 执行排期 → 预算分配 → 效果预估 → 风险提示。",
                "效果预估必须给出依据（历史同类商单数据或行业基准），不可拍脑袋。",
            ]),
            ("## 报价谈判话术", [
                "锚定策略：先展示历史同类商单的达成效果，再给出报价区间。",
                "让步策略：优先让内容权益（如增加一条切片），其次让价格，尽量保住档期。",
                "面对压价，可用“缩减达人数量但保证头部达人排期”替代直接降价。",
            ]),
        ]),
    ]
    for fname, title, sections in content:
        body = [f"# {title}", ""]
        for h, paras in sections:
            body.append(h); body.append("")
            for p in paras:
                body.append(f"- {p}")
            body.append("")
        docs.append((f"playbook/{fname}", title, "\n".join(body)))
    return docs


def fmt_money(v: float) -> str:
    return f"{v/10000:.1f} 万" if v >= 10000 else f"{int(v)} 元"


def render_rate_cards(kols: list[dict]) -> list[tuple[str, str, str]]:
    """刊例文档：每个类目一份，表格形式"""
    by_cat: dict[str, list[dict]] = {}
    for k in kols:
        by_cat.setdefault(k["category"], []).append(k)
    docs = []
    for cat, ks in by_cat.items():
        title = f"{cat}类目达人刊例表（2026 Q3）"
        lines = [f"# {title}", "", f"适用类目：{cat}｜有效期：2026-07-01 至 2026-12-31｜币种：人民币", "",
                 "| 达人ID | 达人昵称 | 平台 | 量级 | 粉丝量 | 主类目-细分 | 1-20秒 | 21-60秒 | 直播专场 | 互动率 | 合作评级 |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
        for k in sorted(ks, key=lambda x: -x["fans_count"]):
            lines.append(
                f"| {k['kol_id']} | @{k['kol_name']} | {k['platform']} | {k['tier']} | "
                f"{k['fans_count']/10000:.1f}w | {k['sub_category']} | ¥{k['price_1_20s']:,} | "
                f"¥{k['price_21_60s']:,} | ¥{k['price_live']:,} | {k['interact_rate']*100:.2f}% | {k['cooperation_level']} |"
            )
        lines += ["", "> 说明：以上为标准刊例价。定制内容（剧情植入、多平台分发）上浮 20%-50%；"
                      "打包 3 位以上达人可享 10%-20% 折扣；复购合作可谈至刊例 70%-85%。"]
        docs.append((f"rate_cards/{cat}类目达人刊例表.md", title, "\n".join(lines)))
    return docs


def render_deal_cases(deals: list[dict], kols: list[dict], brands: list[dict]) -> list[tuple[str, str, str]]:
    """商单案例文档：结案商单出完整案例，在途商单出简要跟踪单"""
    kmap = {k["kol_id"]: k for k in kols}
    bmap = {b["brand_id"]: b for b in brands}
    docs = []
    for d in deals:
        ks = [kmap[i] for i in d["kol_ids"] if i in kmap]
        if not ks:
            continue
        if d["stage"] == "结案" and d["result_metrics"]:
            m = d["result_metrics"]
            title = f"商单案例 {d['deal_id']}｜{d['brand_name']} {d['category']}-{d['sub_category']}"
            L = [f"# {title}", "",
                 f"- 商单编号：{d['deal_id']}",
                 f"- 广告主：{d['brand_name']}（{bmap.get(d['brand_id'], {}).get('industry', '')}）",
                 f"- 类目：{d['category']} / {d['sub_category']}",
                 f"- 投放目标：{d['goal']}",
                 f"- 预算：{fmt_money(d['budget'])}",
                 f"- 执行周期：{d['start_date']} ~ {d['end_date']}",
                 f"- 主运营：{d['owner']}",
                 f"- 目标受众：{d['demand_desc'].split('受众')[-1].rstrip('。') if '受众' in d['demand_desc'] else '见需求描述'}",
                 "", "## 一、客户需求", "", d["demand_desc"], "",
                 "## 二、达人组合与执行方案", "",
                 "| 达人ID | 昵称 | 量级 | 粉丝量 | 类目 | 标签 | 刊例(21-60s) |",
                 "|---|---|---|---|---|---|---|"]
            for k in ks:
                L.append(f"| {k['kol_id']} | @{k['kol_name']} | {k['tier']} | {k['fans_count']/10000:.1f}w | "
                         f"{k['category']}/{k['sub_category']} | {'、'.join(k['tags'])} | ¥{k['price_21_60s']:,} |")
            L += ["", f"- 组合逻辑：{rng.choice(['头部建立信任 + 腰部承接转化 + 尾部铺设口碑', '以腰部达人为主，兼顾曝光与转化', '聚焦垂直达人，追求高转化'])}",
                  f"- 执行节奏：{d['_schedule_note']}，按 30 天标准周期推进。",
                  f"- 预算分配：达人费用 {fmt_money(d['budget']*0.82)}，内容制作 {fmt_money(d['budget']*0.08)}，机动 {fmt_money(d['budget']*0.10)}。",
                  "", "## 三、结案数据", "",
                  f"- 总曝光量：{m['exposure']:,}",
                  f"- 总互动量：{m['interaction']:,}",
                  f"- 互动率：{m['interaction']/max(m['exposure'],1)*100:.2f}%",
                  f"- GMV：{m['gmv']:,} 元",
                  f"- ROI：{m['roi']}",
                  f"- CPM：{m['cpm']} 元",
                  "",
                  "## 四、复盘结论", "",
                  f"- 本单 ROI 为 {m['roi']}，{'达到' if m['roi'] >= 1.0 else '未达到'}立项基准（1.0）。",
                  f"- 有效经验：{rng.choice(['尾部达人互动率显著高于预期，性价比突出，建议后续扩大尾部铺量比例。', '腰部达人 COL 组合效果最稳，可作为该类目标准配置。', '成分科普型内容完播率明显优于口播硬广，建议保留内容框架。'])}",
                  f"- 改进项：{rng.choice(['头部达人曝光达标但转化偏弱，建议后续以品牌露出为目标而非直接带货。', '档期偏紧导致脚本打磨时间不足，建议提前 10 天启动。', '部分达人受众重合度不足，需在初筛阶段强化人群校验。'])}",
                  ]
            docs.append((f"deal_cases/{d['deal_id']}_案例.md", title, "\n".join(L)))
        else:
            title = f"在途商单跟踪单 {d['deal_id']}｜{d['brand_name']} {d['category']}"
            L = [f"# {title}", "",
                 f"- 商单编号：{d['deal_id']}",
                 f"- 广告主：{d['brand_name']}",
                 f"- 类目：{d['category']} / {d['sub_category']}",
                 f"- 当前阶段：{d['stage']}",
                 f"- 投放目标：{d['goal']}",
                 f"- 预算：{fmt_money(d['budget'])}",
                 f"- 负责人：{d['owner']}",
                 f"- 最后更新：{d['updated_at']}",
                 f"- 计划结案：{d['end_date']}",
                 "", "## 需求描述", "", d["demand_desc"], "",
                 "## 候选达人", ""]
            for k in ks:
                L.append(f"- {k['kol_id']} @{k['kol_name']}（{k['tier']}，{k['fans_count']/10000:.1f}w 粉，{k['category']}/{k['sub_category']}，报价 ¥{k['price_21_60s']:,}）")
            L += ["", "## 备注", "", f"- {d['_schedule_note']}"]
            docs.append((f"deal_cases/inflight/{d['deal_id']}_跟踪单.md", title, "\n".join(L)))
    return docs


def main() -> None:
    SYN.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    kols = gen_kols(120)
    brands = gen_brands(40)
    deals = gen_deals(kols, brands, 90)
    followups = gen_followups(deals)

    for name, obj in [("kol_profile", kols), ("brand", brands), ("deal", deals), ("deal_followup", followups)]:
        clean = json.loads(json.dumps(obj, ensure_ascii=False, default=str))
        (SYN / f"{name}.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    (SYN / "rate_card.json").write_text(json.dumps(
        [{k: v for k, v in x.items() if k.startswith("price") or k in
          ("kol_id", "kol_name", "category", "platform", "fans_count", "tier", "interact_rate", "cooperation_level")}
         for x in kols], ensure_ascii=False, indent=2), encoding="utf-8")

    docs = render_platform_rules() + render_playbook() + render_rate_cards(kols) + render_deal_cases(deals, kols, brands)
    for rel, title, body in docs:
        p = RAW / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    staged = {}
    for d in deals:
        staged[d["stage"]] = staged.get(d["stage"], 0) + 1
    print(f"达人 {len(kols)} 条｜广告主 {len(brands)} 条｜商单 {len(deals)} 条｜跟进 {len(followups)} 条")
    print(f"语料文档 {len(docs)} 篇 -> {RAW}")
    print(f"商单阶段分布: {staged}")


if __name__ == "__main__":
    main()
