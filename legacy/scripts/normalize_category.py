"""kol_profile.category 归一化：把 68 个脏类目（截断/错别字/复合词/ID）归并到标准大类。
直接 UPDATE 库，然后删掉旧 rate_cards 文件，由 render_v2_docs 重渲染。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from starmoyu import storage  # noqa: E402

# 标准大类（照 MCN 行业惯例 + 现库主要类目）
CANON = ["游戏", "生活", "剧情搞笑", "美食", "母婴亲子", "旅行", "萌宠", "三农",
         "二次元", "美妆", "测评", "运动健身", "时尚", "汽车", "影视娱乐",
         "艺术文化", "生活家居", "情感", "教育培训", "科技数码", "才艺技能",
         "财经投资", "颜值达人", "舞蹈", "音乐", "随拍", "体育", "房产",
         "食品饮料", "其他"]

# 脏类目 → 大类 映射
MAP = {
    # 复合词：取第一个有效大类
    "母婴宠物": "萌宠",
    "母婴亲子二次元": "母婴亲子", "母婴亲子美食": "母婴亲子",
    "剧情搞笑母婴亲子": "剧情搞笑",
    "测评美妆": "美妆", "美妆测评": "美妆", "美妆时尚": "美妆", "时尚美妆": "美妆",
    "二次元美妆": "二次元", "美妆生活": "美妆", "汽车美妆": "汽车",
    "时尚测评": "测评", "时尚测评美食": "测评", "测评美食": "美食",
    "美食探店": "美食", "三农美食": "三农", "三农没事": "三农",
    "生活家居": "生活家居", "生活剧情搞笑": "生活", "生活美食": "生活",
    "旅行艺术文化": "旅行", "旅行美食": "旅行", "旅行生活": "旅行",
    "旅行时尚": "旅行", "旅行美妆": "旅行", "旅游": "旅行",
    "时尚解说": "时尚", "才艺": "才艺技能", "运动": "运动健身",
    "食品（烟酒）": "食品饮料", "食品饮料": "食品饮料",
    "颜值": "颜值达人", "亲子": "母婴亲子", "园艺": "生活家居",
    # 截断词（Excel 列错位的残片）
    "视娱乐": "影视娱乐", "技数码": "科技数码", "术文化": "艺术文化",
    # 脏值
    "暂无标签": "其他", "其他": "其他", "喊我吃榴莲": "其他",
    "6.87016014440405e+18": "其他",
}


def main():
    with storage.pg_connect() as c:
        cur = c.cursor()
        # 1. 找出所有不在 CANON 里的类目并映射
        cur.execute("SELECT DISTINCT category FROM kol_profile WHERE category IS NOT NULL")
        cats = [r[0] for r in cur.fetchall()]
        changed = 0
        for cat in cats:
            if cat in CANON:
                continue
            target = MAP.get(cat, "其他")
            cur.execute("UPDATE kol_profile SET category=%s WHERE category=%s", (target, cat))
            changed += cur.rowcount
            print(f"  {cat} -> {target} ({cur.rowcount} 行)")
        con = c
        con.commit()
        print(f"\n共归一化 {changed} 行")

        # 2. 删除 sub_category 与 category 相同的冗余
        cur.execute("UPDATE kol_profile SET sub_category=NULL WHERE sub_category=category")
        con.commit()

        # 3. 终态分布
        cur.execute("""SELECT category, count(*) FROM kol_profile
                       WHERE category IS NOT NULL GROUP BY category ORDER BY 2 DESC""")
        rows = cur.fetchall()
        print(f"\n终态类目数: {len(rows)}")
        for r in rows: print(f"  {r[0]}: {r[1]}")


if __name__ == "__main__":
    main()
