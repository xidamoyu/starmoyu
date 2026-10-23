# -*- coding: utf-8 -*-
"""生成评测集 v6：300 题，真值全部由语料正文派生并自动校验。

相对 v5（102 题）的变化，每条都对应审计发现的具体缺陷：

  1. 规模 300 题，分层配额 L1 70 / L2 100 / L3 80 / L4 50。

  2. 真值来源唯一：全部由 eval_corpus_lib 从语料正文正则解析出的字段计算，
     不读 PostgreSQL 的 deal 表——deal.platform_req 与文档「需求描述」段的投放平台
     并不一致，拿它当真值会造出检索永远命不中的题。单真值题必须恰好命中 1 篇，
     聚合题必须落在 [2,10]（真值 > 10 时 Recall@10 结构上限 < 1，指标被窗口截断）。

  3. 题干不得重复：任意两题同层内字符三元组 Jaccard < 0.85（真重复）。

  4. 问法不得集中：每层单一问法模式占比 <= 25%；句子骨架从每类的多个句式池
     轮转抽取，避免「同一句话换 30 个类目」的刷量。

  5. 判别力：L2/L3 题干若同时出现目标商单的品牌名与类目名，退化为字面匹配，丢弃。

  6. 拒答分层：L4 分「干净拒答」（语料零相关，须完整拒答）与「边界拒答」
     （有相邻知识，须部分回答 + 说明缺失）两类，分别统计。

用法：.venv/Scripts/python.exe scripts/build_eval_set_v6.py
产出：reports/eval_set_v6.json
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from eval_corpus_lib import (  # noqa: E402
    inflight, load_deal_docs, load_deal_text, load_doc_chunks, load_rate_cards,
    pick_one, pick_range,
)

OUT = ROOT / "reports" / "eval_set_v6.json"
SEED = 20261023
QUOTA = {"L1": 70, "L2": 100, "L3": 80, "L4": 50}
DUP_JACCARD = 0.85        # 同层两题相似度超过即视为同一道题，丢弃
PATTERN_SHARE_MAX = 0.25  # 单一问法模式占该层题目的比例上限

# 骨架归一化用的槽位词典：实体名（语料里的品牌/昵称/类目）与问法参数。
# 必须在第一次 add() 之前就位，否则骨架判定会把「换实体」当成不同问法，失去作用。
ENTITIES: set[str] = set()
SLOT_WORDS = [
    "种草带货", "推新品", "新品推广", "做口碑测评", "口碑测评", "打品牌曝光", "品牌曝光",
    "哔哩哔哩", "小红书", "抖音", "快手", "视频号",
    "刚聊完需求", "还在提案", "已经签约", "正在执行", "已经结案", "最后丢单",
    "需求沟通", "提案", "签约", "执行", "结案", "丢单", "在途",
]

rng = random.Random(SEED)
items: list[dict] = []
pattern_used: Counter = Counter()
dropped: Counter = Counter()


# ---------------- 判重与入库 ----------------
def _tri(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 3] for i in range(max(0, len(s) - 2))} or {s}


def _jac(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


_TRI: dict[str, set[str]] = {}
SKEL_CAP = 4          # 同一问法骨架最多题数：超过就换句式，杜绝「一句话换 20 个槽位」
_skel_used: Counter = Counter()


def skeleton(q: str) -> str:
    """把题干里的可变槽位（实体名 + 业务参数）抹成占位符，剩下的就是问法骨架。

    阶段一直接把问法参数当模板串替换；实体名由 main() 里的 ENTITIES 填充。
    """
    s = q
    for w in sorted(SLOT_WORDS, key=len, reverse=True):   # 先换参数短语
        s = s.replace(w, "⟨S⟩")
    for e in sorted(ENTITIES, key=len, reverse=True):     # 再换实体名
        s = s.replace(e, "⟨E⟩")
    return s


def add(layer: str, query: str, labels: dict[str, str], pattern: str,
        rule: str, note: str = "") -> bool:
    """入库前统一校验：真值结构 + 题干重复 + 问法骨架上限。任一条不满足即丢弃并计数。"""
    n = len(labels)
    n_direct = sum(1 for v in labels.values() if v == "direct")
    if layer in ("L1", "L2") and n_direct != 1:
        dropped[f"{layer}_direct非1"] += 1
        return False
    if layer == "L3" and not (2 <= n <= 10):
        dropped["L3_真值越界"] += 1
        return False
    if layer == "L4" and n:
        dropped["L4_带标注"] += 1
        return False

    tri = _tri(query)
    for x in items:
        if x["difficulty"] != layer:
            continue
        if _jac(tri, _TRI.setdefault(x["query"], _tri(x["query"]))) >= DUP_JACCARD:
            dropped["题干重复"] += 1
            return False

    # 骨架上限只约束「聚合类/开放类」题目：这类题的真值是文档集合，
    # 骨架相同意味着检索路径完全相同，换槽位就是在刷量。
    # 单文档精确题（L1 刊例表、L2 昵称问价）题干带唯一标识（达人昵称），
    # 真值必然唯一、答案各不相同，骨架相同属正常检索测试，不受此限。
    single_doc_lookup = (
        layer in ("L1", "L2")
        and n_direct == 1
        and any(f.startswith("rate_cards/") for f in labels)
    )
    sk = skeleton(query)
    if not single_doc_lookup:
        if _skel_used[sk] >= SKEL_CAP:
            dropped["问法骨架超限"] += 1
            return False
        _skel_used[sk] += 1
    _TRI[query] = tri
    pattern_used[pattern] += 1
    iid = f"{layer}-{(sum(1 for x in items if x['difficulty'] == layer) + 1):03d}"
    items.append({"id": iid, "difficulty": layer, "query": query, "labels": labels,
                  "pattern": pattern, "truth_rule": rule, "note": note})
    return True


def room(layer: str) -> int:
    return QUOTA[layer] - sum(1 for x in items if x["difficulty"] == layer)


def rot(pool: list[str], key: str) -> str:
    """按 key 稳定打散取句式。

    用 sum(ord) 取模的问题：`才艺`/`家居`/`旅行` 这类同类目名的字符和常常同余，
    会连续落到同一句式上，把本来分散的问法又挤成一簇。改用 sha1 前缀取模，
    分布与字符相邻性无关。
    """
    import hashlib
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    return pool[h % len(pool)]


# ---------------- 语料 ----------------
DEALS = load_deal_docs()
RATE = load_rate_cards()
P1, P2, P3 = ("policies/抖音星图下单与结算规则.md",
              "policies/巨量星图达人合作常见问题.md",
              "policies/商单立项与执行SOP.md")
B1, B2, B3 = ("playbook/达人筛选评估框架.md", "playbook/美妆类目投放方法论.md",
              "playbook/提案与报价话术要点.md")
IMP = next(iter(load_doc_chunks("imports/")), None)

BRANDS = sorted({d["brand"] for d in DEALS if d["brand"]})
GOALS = sorted({d["goal"] for d in DEALS if d["goal"]})
PLATS = sorted({d["platform"] for d in DEALS if d["platform"]})
STAGES = sorted({d["stage"] for d in DEALS if d["stage"]})
CATS = sorted({d["category"] for d in DEALS if d["category"]})

# 填充骨架归一化词典：品牌、类目、达人昵称都算槽位实体
ENTITIES.update(BRANDS)
ENTITIES.update(CATS)
ENTITIES.update(p["nick"].lstrip("@") for p in RATE if p["nick"])
ENTITIES.update(p["nick"] for p in RATE if p["nick"])

# 目标短语两套写法：定语式用于「{c}类目做{g}的商单」这类模板（模板自带动词），
# 动词式用于「{b}{g}那单」这类模板（需要自己带动词）。混用会产生「做做口碑测评」的病句。
GOAL_ATTR = {"种草带货": "种草带货", "新品推广": "推新品",
             "口碑测评": "口碑测评", "品牌曝光": "品牌曝光"}
GOAL_VERB = {"种草带货": "做种草带货", "新品推广": "推新品",
             "口碑测评": "做口碑测评", "品牌曝光": "打品牌曝光"}
STAGE_PHRASE = {"需求沟通": "刚聊完需求", "提案": "还在提案", "签约": "已经签约",
                "执行": "正在执行", "结案": "已经结案", "丢单": "最后丢单"}


# =====================================================================
# L1 精确查找（70）：政策条文 + 刊例表单行
# =====================================================================
L1_FACTS = [
    ("星图订单创建后达人多少小时内必须确认接单？", P1, "订单创建后 48 小时内确认", "P_howlong"),
    ("定向邀约模式下单次最多能邀约多少位达人？", P1, "定向邀约单次最多 20 位", "P_maxnum"),
    ("平台服务费向广告主和达人侧各收取多少？", P1, "广告主 5%、达人侧 10%", "P_fee"),
    ("达人报价调整需要提前多少天生效？", P1, "报价调整提前 7 个自然日", "P_days"),
    ("内容发布后的数据回传有哪几个时间节点？", P1, "24 小时/7 天/30 天", "P_nodes"),
    ("定制内容在标准刊例基础上上浮多少？", P1, "定制内容上浮 20%-50%", "P_uprate"),
    ("达人粉丝量的头部、腰部、尾部分界线是多少？", P2, "头部 500 万以上/腰部 50-500 万/尾部 50 万以下", "P_threshold"),
    ("S 级达人能享受什么待遇？", P2, "优先档期、寄样与脚本共创", "P_benefit"),
    ("首次合作报价通常是刊例的什么区间？", P2, "首次 85%-100%，复购 70%-85%", "P_range"),
    ("打包多位达人一般能争取到多少折扣？", P2, "3 位以上打包 10%-20%", "P_discount"),
    ("纯佣金模式下达人分成比例是多少？", P2, "GMV 的 15%-30%", "P_split"),
    ("头部达人的档期通常需要提前多久锁定？", P2, "提前 20-30 天", "P_lead"),
    ("商单立项的预算门槛是多少？", P3, "预算 ≥ 10 万或年框客户", "P_gate"),
    ("预算分配里达人费用、内容制作、机动的占比各是多少？", P3, "达人 75%-85%/内容 5%-10%/机动 5%-15%", "P_alloc"),
    ("三十天标准周期里哪一段用来做报价谈判和档期锁定？", P3, "D4-D7", "P_window"),
    ("结案后几个工作日内要完成归档？", P3, "5 个工作日内", "P_deadline"),
    ("ROI 低于立项基准的商单在结案时要做什么？", P3, "结案报告中说明原因与改进项", "P_action"),
    ("达人匹配的四条优先原则分别是什么？", P3, "类目匹配度/人群重合度/性价比/履约稳定性", "P_principles"),
    ("五维评估模型各维度的权重是多少？", B1, "类目 25%/人群 25%/内容 20%/性价比 20%/履约 10%", "P_weights"),
    ("达人筛选的硬性排除项有哪些？", B1, "90 天刷量违规一票否决等", "P_exclude"),
    ("美妆投放推荐什么样的达人漏斗组合？", B2, "1 头部 + 3-5 腰部 + 5-10 尾部", "P_funnel"),
    ("高客单精华类产品优先选哪类达人？", B2, "成分党、配方师类达人", "P_prefer"),
    ("美妆类目 CPM 和互动率的参考区间是多少？", B2, "CPM 25-60 元、互动率 2%-5%", "P_kpi"),
    ("腰部达人和头部达人的 ROI 基准分别是多少？", B2, "腰部 ≥1.2，头部 0.8", "P_benchmark"),
    ("标准提案包含哪几个部分？", B3, "需求理解→…→风险提示 七部分", "P_parts"),
    ("面对客户压价，话术上优先让什么、其次让什么？", B3, "先让内容权益，其次让价格", "P_priority"),
]
for q, doc, note, pat in L1_FACTS:
    add("L1", q, {doc: "direct"}, pat, "政策/方法论文档明写条文", note)

# 刊例表单达人行：昵称在全语料唯一才可用（否则无法定位到唯一文档）
# 附加 assist 标注：该达人还出现在哪些商单文档的「达人组合表 / 候选达人清单」里。
# 那些文档能给出同一条报价，但不回答「这条报价是否合理」，属辅助参考（计 0.5 分），
# 不标出来就会被审计判成漏标。
_DEAL_TEXT = load_deal_text()


def _assist_docs(nick: str) -> dict[str, str]:
    bare = nick.lstrip("@")
    return {f: "assist" for f, t in _DEAL_TEXT.items() if bare and bare in t}
_nick_cnt = Counter(p["nick"] for p in RATE)
# 昵称本身含类目名的（「大亮旅行记」含「旅行」、「美食作家王刚」含「美食」）不能用于
# 不含类目词的题：题干会无意中泄漏类目，退化成字面匹配。这里直接排除。
_CAT_WORDS = sorted({d["category"] for d in DEALS if d["category"]}, key=len, reverse=True)
_uniq_people = [
    p for p in RATE
    if _nick_cnt[p["nick"]] == 1 and p["price"] and p["nick"]
    and not any(w in p["nick"].lstrip("@") for w in _CAT_WORDS)
]
rng.shuffle(_uniq_people)

L1_NICK_FORMS = [
    ("P_nick_price", "{n} 的 21-60 秒刊例价是报多少？"),
    ("P_nick_fans", "{n} 粉丝多少、属于哪一档量级？"),
    ("P_nick_play", "{n} 的平均播放能跑到什么水平？"),
    ("P_nick_cpm", "{n} 的 CPM 是多少？"),
    ("P_nick_rate", "{n} 的互动率表现怎么样？"),
    ("P_nick_rating", "{n} 的合作评级是什么？"),
    ("P_nick_cat", "{n} 主攻哪个细分方向？"),
]
_k = 0
for p in _uniq_people:
    if room("L1") <= 0:
        break
    pat, tpl = L1_NICK_FORMS[_k % len(L1_NICK_FORMS)]
    _k += 1
    note = (f"{p['cat']}｜{p['tier']}｜粉丝 {p['fans_w']}w｜刊例 {p['price']}｜"
            f"均播 {p['play']}｜CPM {p['cpm']}｜互动 {p['rate']}｜{p['rating']}")
    add("L1", tpl.format(n=p["nick"].lstrip("@")),
        {p["file"]: "direct", **_assist_docs(p["nick"])}, pat,
        "刊例表单达人行，昵称全语料唯一（真值必然唯一命中）", note)

# =====================================================================
# L2 语义改写（100）：口语→条文 / 昵称→刊例 / 指代→商单
# =====================================================================
COLLOQ = [
    ("腰部达人谈价一般能压下来多少？", IMP, "Q_price_nego"),
    ("KOC 合作要注意什么、怎么报价？", IMP, "Q_koc"),
    ("大促前头部达人档期约不上怎么处理？", IMP, "Q_peak"),
    ("品牌方年框返点一般什么行情？", IMP, "Q_rebate"),
    ("达人延期发布要怎么扣钱？", IMP, "Q_delay"),
    ("医美、保健这类特殊品类投放要注意什么？", P1, "Q_special"),
    ("广告里哪些词是绝对不能用的？", P1, "Q_banned"),
    ("怎么判断达人数据有没有注水？", P2, "Q_fake"),
    ("广告主拖着不结款会有什么后果？", P2, "Q_overdue"),
    ("选达人的时候除了粉丝量还要看什么？", B1, "Q_beyond_fans"),
    ("美妆内容里能不能直接说美白祛斑？", B2, "Q_claim"),
    ("做效果预估的时候依据从哪来？", B3, "Q_forecast"),
    ("客户压价的时候该怎么应对？", B3, "Q_counter"),
    ("提案里怎么排执行节奏？", B3, "Q_rhythm"),
    ("星图那边下单以后多久之内达人要回应？", P1, "Q_resp"),
    ("服务费到底谁掏、掏几个点？", P1, "Q_fee_who"),
    ("报价想改动的话得提前多久打招呼？", P1, "Q_price_change"),
    ("粉丝多少算头部、多少算腰尾部？", P2, "Q_tier_line"),
    ("第一次合作能给多少让价空间？", P2, "Q_first"),
    ("一次打包好几个达人划算吗？", P2, "Q_bundle"),
    ("只按卖货分成的话怎么分？", P2, "Q_commission"),
    ("大号档期要提前多久去占？", P2, "Q_slot"),
    ("多少预算才值得单独立项？", P3, "Q_budget_gate"),
    ("一个项目的钱怎么在达人、内容、备用金之间分？", P3, "Q_splitting"),
    ("结案材料多久之内必须归好档？", P3, "Q_filing"),
    ("效果没达标结案的时候要写点什么？", P3, "Q_underperform"),
    ("挑达人优先看哪几条？", P3, "Q_match"),
    ("评估达人那套模型各项占多少分？", B1, "Q_weights"),
    ("哪些达人是一票否决的？", B1, "Q_veto"),
    ("美妆投放一般配几个头部几个腰部？", B2, "Q_funnel"),
    ("卖贵价精华该找什么样的博主？", B2, "Q_premium"),
    ("美妆的 CPM 和互动率正常在什么水平？", B2, "Q_kpi"),
    ("腰部和头部的投产比基准差多少？", B2, "Q_benchmark"),
    ("一份标准提案要写哪几块？", B3, "Q_proposal"),
    ("客户砍价先让哪一步？", B3, "Q_concede"),
    ("数据什么时候回传、回传几次？", P1, "Q_callback"),
    ("定制内容要加钱吗，加多少？", P1, "Q_custom"),
    ("达人等级是怎么分的？", P2, "Q_s_tier"),
    ("合作过的再合作有没有优惠？", P2, "Q_repeat"),
    ("纯佣模式平台抽多少？", P2, "Q_pure_gmv"),
    ("这单要达到什么标准才算不亏？", P3, "Q_roi_base"),
]
for q, doc, pat in COLLOQ:
    if room("L2") <= 0:
        break
    if doc:
        add("L2", q, {doc: "direct"}, pat, "口语表达 → 条文，与文档用词字面零重合")

L2_NICK_FORMS = [
    ("P_nick_who", "{n} 现在什么价位？"),
    ("P_nick_ask", "想找 {n} 合作，大概要准备多少预算？"),
    ("P_nick_worth", "{n} 报的价合理吗？"),
    ("P_nick_brief", "{n} 接单的话数据撑得住吗？"),
    ("P_nick_alt", "有没有 {n} 这个号的报价资料？"),
]
_j = 0
for p in _uniq_people:
    if room("L2") <= 0:
        break
    pat, tpl = L2_NICK_FORMS[_j % len(L2_NICK_FORMS)]
    _j += 1
    add("L2", tpl.format(n=p["nick"].lstrip("@")),
        {p["file"]: "direct", **_assist_docs(p["nick"])}, pat,
        "口语问价 → 刊例表，题干不含类目名", f"{p['cat']}｜{p['tier']}｜{p['price']}")

# 指代型：品牌 × 目标（±平台/阶段）唯一命中 1 篇
D_FORMS = {
    "D_roi": ["{b}{g}那单最后的投产比是多少？", "{b}{g}那个项目跑出来的 ROI 怎样？"],
    "D_stage": ["{b}{g}那个单子现在到哪一步了？", "{b}{g}的单子眼下什么进展？"],
    "D_lost": ["{b}{g}那单最后为什么没成？", "{b}{g}这个项目是怎么黄的？"],
    "D_budget": ["{b}{g}那单预算给了多少？", "{b}{g}项目批了多少预算？"],
    "D_qty": ["{b}{g}那单投了几个达人？", "{b}{g}项目上了多少位达人？"],
    "D_exp": ["{b}{g}那单曝光跑出来多少？", "{b}{g}项目的曝光量是多少？"],
    "D_plat": ["{b}投{p}做{g}的那单进展如何？", "{b}在{p}上{g}的那个项目怎么样？"],
    "D_exec": ["{b}{g}且正在执行的那单效果怎么样？", "{b}{g}正在跑的那单数据如何？"],
}
_deic: list[tuple[str, dict, str, str]] = []
for b in BRANDS:
    for g in GOALS:
        f = pick_one(DEALS, lambda d, bb=b, gg=g: d["brand"] == bb and d["goal"] == gg, b + g)
        if f:
            for key in ("D_roi", "D_stage", "D_lost", "D_budget", "D_qty", "D_exp"):
                tpl = rot(D_FORMS[key], b + g + key)
                _deic.append((tpl.format(b=b, g=GOAL_VERB[g], p="抖音"),
                              {f: "direct"}, key, f"{b}×{g}"))
            continue
        for p in PLATS:
            f2 = pick_one(DEALS, lambda d, bb=b, gg=g, pp=p:
                          d["brand"] == bb and d["goal"] == gg and d["platform"] == pp, b + g + p)
            if f2:
                tpl = rot(D_FORMS["D_plat"], b + g + p)
                _deic.append((tpl.format(b=b, g=GOAL_VERB[g], p=p), {f2: "direct"},
                              "D_plat", f"{b}×{g}×{p}"))
                break
        for s in STAGES:
            f3 = pick_one(DEALS, lambda d, bb=b, gg=g, ss=s:
                          d["brand"] == bb and d["goal"] == gg and d["stage"] == ss, b + g + s)
            if f3:
                key = "D_exec" if s == "执行" else "D_stage"
                _deic.append((f"{b}{GOAL_VERB[g]}、{STAGE_PHRASE[s]}的那个项目，具体情况是什么？",
                              {f3: "direct"}, key, f"{b}×{g}×{s}"))
                break

rng.shuffle(_deic)
for q, labels, pat, note in _deic:
    if room("L2") <= 0:
        break
    add("L2", q, labels, pat, "品牌+投放目标(+平台/阶段)唯一命中 1 篇", note)

# =====================================================================
# L3 多约束聚合（80）：真值 2-10 篇
# =====================================================================
L3_POOL: list[tuple[str, dict, str, str]] = []

# 每个句式池至少 8 句：类目有 30 个，池子太浅（2-3 句）会让 30 个类目之间
# 的题干字符相似度顶到 0.8，被审计判为「问法单一」。池子深度是判据的分母。
CAT_FORMS = {
    "C_case": [
        "{c}这个类目结过案的案例都有哪些？",
        "{c}线已经收尾的项目能拿出哪些来讲？",
        "{c}类目历史上做过哪些交付完成的案子？",
        "手上有哪些{c}的结案复盘可以用？",
        "想看看{c}做完了的项目清单，能列出来吗？",
        "{c}这边已经收口、可以复述成果的单子有哪几个？",
        "给我一份{c}的成案清单。",
        "{c}领域我们做过哪些已经交付的项目？",
    ],
    "C_live": [
        "{c}这条线现在手上还有哪些在途单子？",
        "{c}类目眼下有几个单子没跑完？",
        "{c}方向现在进行中的合作有哪些？",
        "{c}目前没结案的项目有几个，都是什么？",
        "眼下{c}有几单还在推进？",
        "{c}这条线在途项目的清单给我一份。",
        "{c}类目现在压着哪些没交付的单？",
        "{c}有哪些正在走的单子还没收尾？",
    ],
    "C_lost": [
        "{c}类目今年丢过哪些单？",
        "{c}这条线没谈成的都有哪几个？",
        "{c}方向有哪些客户最后没签下来？",
        "{c}类目废掉的商单是哪些？",
        "想复盘一下{c}没做成的单子，有哪几个？",
        "{c}这边谈崩过的项目都包括哪些？",
    ],
    "C_big": [
        "{c}类目做过哪些预算十万以上的单？",
        "{c}方向投得比较大的项目有哪些？",
        "{c}类目里预算规模靠前的项目是哪几个？",
        "{c}这边有没有上十万的大项目？",
        "{c}类目投入较大的商单清单？",
        "{c}方向金额比较高的单子列一下。",
    ],
}
for c in CATS:
    for key, pred in [
        ("C_case", lambda d, cc=c: d["kind"] == "case" and d["category"] == cc),
        ("C_live", lambda d, cc=c: inflight(d) and d["category"] == cc),
        ("C_lost", lambda d, cc=c: d["stage"] == "丢单" and d["category"] == cc),
        ("C_big", lambda d, cc=c: d["category"] == cc and (d["budget_wan"] or 0) >= 10),
    ]:
        files = pick_range(DEALS, pred)
        if not files:
            dropped["L3_类目条件越界"] += 1
            continue
        tpl = rot(CAT_FORMS[key], c + key)
        L3_POOL.append((tpl.format(c=c), {f: "direct" for f in files}, key,
                        f"{c}｜{key}｜{len(files)} 篇"))

CGP_FORMS = [
    "{c}类目做{g}的商单有哪些？",
    "{c}那边{g}方向都投过哪几个单？",
    "{c}类目配合{g}的目标做过什么单子？",
    "{c}这边以{g}为目的的项目有哪些？",
    "{c}类目{g}类的合作都包括哪些？",
    "跟{c}做{g}相关的商单帮我列一下。",
]
CGPL_FORMS = [
    "{c}那边投{p}做{g}的都有哪些单子？",
    "{c}类目在{p}上{g}的单子列一下？",
    "{c}类目里投{p}、做{g}的是哪几个项目？",
    "{c}在{p}渠道做{g}的合作有哪些？",
    "投{p}的{c}{g}项目有哪些？",
    "{c}类目{p}渠道的{g}单子给我看看。",
]
CGLIVE_FORMS = [
    "{c}类目还在跑、目标是{g}的有哪些？",
    "{c}类目{g}方向还没结案的有哪几个？",
    "{c}这边{g}的在途单子有几个？",
    "{c}类目{g}项目现在进行中的有哪些？",
    "{c}还有哪些{g}的单子没做完？",
    "{c}类目{g}方向上尚未收尾的项目？",
]
for c in CATS:
    for g in GOALS:
        files = pick_range(DEALS, lambda d, cc=c, gg=g: d["category"] == cc and d["goal"] == gg)
        if files:
            tpl = rot(CGP_FORMS, c + g)
            L3_POOL.append((tpl.format(c=c, g=GOAL_ATTR[g]), {f: "direct" for f in files},
                            "C_cgp", f"{c}×{g}｜{len(files)} 篇"))
        for p in PLATS:
            files2 = pick_range(DEALS, lambda d, cc=c, gg=g, pp=p:
                                d["category"] == cc and d["goal"] == gg and d["platform"] == pp)
            if files2:
                tpl = rot(CGPL_FORMS, c + g + p)
                L3_POOL.append((tpl.format(c=c, g=GOAL_ATTR[g], p=p),
                                {f: "direct" for f in files2}, "C_cgpl",
                                f"{c}×{g}×{p}｜{len(files2)} 篇"))
        files3 = pick_range(DEALS, lambda d, cc=c, gg=g:
                            inflight(d) and d["category"] == cc and d["goal"] == gg)
        if files3:
            tpl = rot(CGLIVE_FORMS, c + g)
            L3_POOL.append((tpl.format(c=c, g=GOAL_ATTR[g]), {f: "direct" for f in files3},
                            "C_cglive", f"{c}×{g}×在途｜{len(files3)} 篇"))

BRAND_FORMS = {
    "B_all": ["{b}这个品牌一共合作过哪些单子？", "{b}跟我们都做过什么项目？",
              "{b}历史上接的商单有哪些？", "{b}整体合作情况汇总一下？",
              "{b}在我们这边投过什么？", "{b}的合作记录帮我调出来。"],
    "B_live": ["{b}现在还有几单没结案，分别什么情况？", "{b}手上没跑完的单子有几个？",
               "{b}目前在进行中的项目有哪些？", "{b}还有哪些单子没交付？",
               "{b}在途项目的情况是怎样的？", "{b}眼下压着几个未完成的项目？"],
    "B_case": ["{b}有哪些已经结案、能拿来讲的案例？", "{b}已经交付的项目都是哪些？",
               "{b}做完了的案子有哪些？", "{b}可复用的成案清单？",
               "{b}哪些项目已经收口了？", "{b}交付完成的案例给我列一下。"],
    "B_lost": ["{b}有没有谈崩的单子？", "{b}丢过哪些单？", "{b}哪些项目最后没成？",
               "{b}未签约的合作是哪些？", "{b}放弃掉的项目有哪些？", "{b}黄掉的单子有吗？"],
}
for b in BRANDS:
    for key, pred in [
        ("B_all", lambda d, bb=b: d["brand"] == bb),
        ("B_live", lambda d, bb=b: d["brand"] == bb and inflight(d)),
        ("B_case", lambda d, bb=b: d["brand"] == bb and d["kind"] == "case"),
        ("B_lost", lambda d, bb=b: d["brand"] == bb and d["stage"] == "丢单"),
    ]:
        files = pick_range(DEALS, pred)
        if not files:
            continue
        tpl = rot(BRAND_FORMS[key], b + key)
        L3_POOL.append((tpl.format(b=b), {f: "direct" for f in files}, key,
                        f"{b}｜{key}｜{len(files)} 篇"))

for p in PLATS:
    for g in GOALS:
        files = pick_range(DEALS, lambda d, pp=p, gg=g: d["platform"] == pp and d["goal"] == gg)
        if files:
            L3_POOL.append((f"{p}上做{GOAL_ATTR[g]}的商单有哪些？",
                            {f: "direct" for f in files}, "X_pg", f"{p}×{g}｜{len(files)} 篇"))
    for s in STAGES:
        files = pick_range(DEALS, lambda d, pp=p, ss=s: d["platform"] == pp and d["stage"] == ss)
        if files:
            L3_POOL.append((f"{p}这边{STAGE_PHRASE[s]}的单子有哪些？",
                            {f: "direct" for f in files}, "X_ps", f"{p}×{s}｜{len(files)} 篇"))
    files = pick_range(DEALS, lambda d, pp=p: d["platform"] == pp and inflight(d))
    if files:
        L3_POOL.append((f"{p}渠道现在在途的单子都有哪些？",
                        {f: "direct" for f in files}, "X_pinflight", f"{p}×在途｜{len(files)} 篇"))

CROSS = [
    ("给一个彩妆品牌写方案，历史案例和类目打法两部分资料帮我拉齐。",
     {"rate_cards/美妆类目达人刊例表.md": "direct", B2: "direct"}, "Y_cross2"),
    ("汽车类目要出投放方案，报价基准和话术要点我都要。",
     {"rate_cards/汽车类目达人刊例表.md": "direct", B3: "direct"}, "Y_cross2"),
    ("要同时看达人筛选标准、类目方法论和下单规则，帮我一起找出来。",
     {B1: "direct", B2: "direct", P1: "direct"}, "Y_cross3"),
    ("做提案前想把达人评估框架和报价话术对照着看一遍。",
     {B1: "direct", B3: "direct"}, "Y_cross2"),
    ("结算规则和立项流程这两份制度我都要核对。",
     {P1: "direct", P3: "direct"}, "Y_cross2"),
]
for q, labels, pat in CROSS:
    L3_POOL.append((q, labels, pat, "跨文档类型"))

rng.shuffle(L3_POOL)
for q, labels, pat, note in L3_POOL:
    if room("L3") <= 0:
        break
    add("L3", q, labels, pat, "多维聚合，真值 2-10 篇（Recall@10 上限为 1）", note)

# =====================================================================
# L4 拒答（50）：25 干净 + 25 边界
# =====================================================================
CLEAN = [
    "拼多多店铺的达人带货佣金政策是什么？", "京东自营品牌的达人合作流程怎么走？",
    "微信视频号达人互选平台的报价规则？", "TikTok 海外达人合作的结算方式？",
    "快手磁力聚星平台的达人报价表？", "小红书蒲公英平台的服务费比例是多少？",
    "虚拟主播的合作报价在哪个文档里？", "数字人直播在星图上怎么下单？",
    "私域流量和社群运营的方法论文档有吗？", "B 站花火平台的接单门槛是多少？",
    "微博微任务的下单流程是怎样的？", "淘宝逛逛达人的结算周期是多久？",
    "知乎知任务平台的返点政策？", "得物达人合作怎么报价？",
    "抖音团购达人怎么结算佣金？", "视频号小店的保证金是多少？",
    "小红书笔记的薯条投放怎么算钱？", "快手粉条的价格体系是什么？",
    "淘宝直播的坑位费一般多少？", "亚马逊影响者计划怎么报名？",
    "美团达人的探店合作报价？", "大众点评笔记的达人招募规则？",
    "Instagram 达人合作的付款周期？", "YouTube 品牌植入的报价规范？",
    "跨境电商的达人佣金税则怎么算？",
]
BOUNDARY = [
    "明星艺人的代言报价怎么看？", "品牌自播间怎么和达人矩阵配合？",
    "坑位费加佣金的模式你们怎么核算？", "达人的税务代扣代缴是怎么处理的？",
    "年货节期间达人档期怎么锁？", "医美类目在星图上投放需要什么特殊资质？",
    "达人账号被平台限流了该怎么补救？", "跨境保税仓发货的商单怎么结算？",
    "品牌方要求对赌销量的话怎么设计条款？", "达人经纪公司的分成怎么走账？",
    "直播场次的实时数据怎么回传？", "达人临时爽约的违约金怎么定？",
    "海外达人寄样的关税谁承担？", "商单内容被平台下架了怎么申诉？",
    "品牌方想买断达人肖像权怎么定价？", "多平台同期投放的排期怎么协调？",
    "达人矩阵的粉丝重叠度怎么去重计算？", "内容二次剪辑授权怎么谈？",
    "品牌方要求独家排他条款怎么处理？", "达人直播和短视频的预算怎么分配？",
    "退货率高的类目怎么核算实际 ROI？", "预付和尾款的账期一般怎么定？",
    "达人口碑危机公关的处理流程？", "商单内容的知识产权归谁？",
    "平台规则变更导致内容违规谁担责？",
]
# 拒答题也要分类型打标，否则同一句式占比会顶到 50%，掩盖"全部是同一类反问"的问题。
# 拒答题的问法维度用「在问什么」划分，而不是「问哪个平台」：
# 平台是语料覆盖的客观事实（只有星图），不是问法差异；按平台归类会把
# 「拼多多佣金」和「京东流程」这两道完全不同的题算成同一问法。
CLEAN_PAT = {
    "价格": "R_clean_price",       # 佣金/报价/坑位费/保证金
    "流程": "R_clean_process",     # 下单/报名/接单/招募
    "结算": "R_clean_settle",      # 结算方式/周期/返点
    "规则": "R_clean_rule",        # 服务费比例/投放计费
}
BOUND_PAT = {
    "法务": "R_bound_legal",      # 肖像权、知识产权、违约、税务
    "运营": "R_bound_ops",        # 限流、爽约、改期、数据回传
    "财务": "R_bound_finance",    # 账期、对赌、ROI 核算
    "流程": "R_bound_process",    # 报备、申诉、排期、授权
}


def _clean_kind(q: str) -> str:
    if any(k in q for k in ("佣金", "报价", "坑位费", "保证金", "身价")):
        return "价格"
    if any(k in q for k in ("流程", "报名", "下单", "招募", "接单", "怎么合作")):
        return "流程"
    if any(k in q for k in ("结算", "周期", "返点", "付款", "税则")):
        return "结算"
    return "规则"


def _bound_kind(q: str) -> str:
    if any(k in q for k in ("肖像", "知识产权", "违约", "税务", "担责", "授权")):
        return "法务"
    if any(k in q for k in ("账期", "对赌", "ROI", "尾款", "预付", "核算", "预算怎么")):
        return "财务"
    if any(k in q for k in ("限流", "爽约", "档期", "排期", "回传", "下架", "申诉")):
        return "运营"
    return "流程"


for q in CLEAN:
    if room("L4") <= 0:
        break
    add("L4", q, {}, CLEAN_PAT[_clean_kind(q)],
        "语料零相关内容，正确行为=完整拒答", "平台/业态在语料中不存在")
for q in BOUNDARY:
    if room("L4") <= 0:
        break
    add("L4", q, {}, BOUND_PAT[_bound_kind(q)],
        "语料有相邻知识，正确行为=部分回答+明确说明缺失",
        "可引用相邻条文但不足以完整回答，禁止编造")

# =====================================================================
# 校验与落盘
# =====================================================================
errs: list[str] = []
ids = [x["id"] for x in items]
dup = [k for k, v in Counter(ids).items() if v > 1]
if dup:
    errs.append(f"ID 重复: {dup[:5]}")

# 判别力：L2/L3 同时出现目标商单的品牌名与类目名 → 退化为字面匹配
_deal_by_file = {d["file"]: d for d in DEALS}
for it in items:
    if it["difficulty"] not in ("L2", "L3"):
        continue
    for f in it["labels"]:
        d = _deal_by_file.get(f)
        if d and d["brand"] and d["brand"] in it["query"] \
                and d["category"] and d["category"] in it["query"]:
            errs.append(f"{it['id']} 题干同含品牌名与类目名：{it['query'][:30]}")

# 真值结构独立复核（生成时已拦，这里再验一遍）
_deal_files = {d["file"] for d in DEALS}
for it in items:
    n = len(it["labels"])
    n_dir = sum(1 for v in it["labels"].values() if v == "direct")
    if it["difficulty"] in ("L1", "L2") and n_dir != 1:
        errs.append(f"{it['id']} 单真值题 direct 标注 {n_dir} 篇")
    if it["difficulty"] == "L3" and not (2 <= n <= 10):
        errs.append(f"{it['id']} 聚合题标注 {n} 篇越界")
    if it["difficulty"] == "L4" and n:
        errs.append(f"{it['id']} 拒答题带标注")
    for f in it["labels"]:
        if f.startswith("deal_cases/") and f not in _deal_files:
            errs.append(f"{it['id']} 标注文件不在语料: {f}")

# 配额与问法分布
layer_cnt = Counter(x["difficulty"] for x in items)
for L, want in QUOTA.items():
    got = layer_cnt.get(L, 0)
    if got < want * 0.85:
        errs.append(f"{L} 题量 {got} 低于配额 {want} 的 85%")
for L in QUOTA:
    sub = Counter(x["pattern"] for x in items if x["difficulty"] == L)
    tot = sum(sub.values()) or 1
    top, cnt = (sub.most_common(1) or [("", 0)])[0]
    if cnt / tot > PATTERN_SHARE_MAX:
        errs.append(f"{L} 单一问法 {top} 占 {cnt / tot:.0%}，超过 {PATTERN_SHARE_MAX:.0%}")

if errs:
    print("校验失败：")
    for e in errs:
        print("  -", e)
    raise SystemExit(1)

payload = {
    "eval_set_version": "v6-2026-10-23",
    "corpus_scale": {"chunks": 1809, "documents": 434, "kol": 14289, "deals": 400},
    "scoring_note": "文档级标注：direct=直接回答（计 1 分），assist=辅助参考（计 0.5 分）。"
                    "L4 分「干净拒答」与「边界拒答」两类分别统计。",
    "difficulty_definition": "L1=精确查找（语料明写）；L2=语义改写（口语/昵称/指代）；"
                             "L3=多约束聚合（真值 2-10 篇）；L4=否定与边界（拒答）",
    "design_note": "真值全部由语料正文派生：单真值题必须唯一命中 1 篇，聚合题真值上限 10 篇"
                   "以保证 Recall@10 可达 1；同层任意两题题干字符三元组相似度 < 0.85；"
                   "单一问法模式占比不超过 25%。",
    "generation": {"seed": SEED, "quota": QUOTA, "dup_jaccard": DUP_JACCARD,
                   "pattern_share_max": PATTERN_SHARE_MAX,
                   "pattern_counts": dict(pattern_used), "dropped": dict(dropped)},
    "layer_counts": dict(layer_cnt),
    "items": items,
}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"写出 {OUT.name}：{len(items)} 题 {dict(layer_cnt)}")
for L in ("L1", "L2", "L3", "L4"):
    sub = Counter(x["pattern"] for x in items if x["difficulty"] == L)
    print(f"  {L} 问法 {len(sub)} 种，最多 {sub.most_common(1)}")
print("L3 真值篇数分布:", sorted(len(x["labels"]) for x in items if x["difficulty"] == "L3"))
print("丢弃:", dict(dropped.most_common(8)))
