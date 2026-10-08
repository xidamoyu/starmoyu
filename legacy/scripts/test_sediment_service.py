"""沉淀流服务单测：渲染/经验提炼/增量入库幂等。只读测试商单 DC20250005，不动其他数据。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server.sediment_service import (  # noqa: E402
    extract_lessons, render_case_md, ingest_document_incremental)
from starmoyu import storage  # noqa: E402

DEAL = "DC20250005"


def test_extract_lessons():
    """经验提炼：给含拒绝/返点/档期的流水，应抽出对应要点。"""
    fs = [
        {"action_type": "电话", "note": "K03867 团队拒绝，档期冲突"},
        {"action_type": "约见", "note": "品牌方返点10，投流反20"},
        {"action_type": "催稿", "note": "改稿第2次，要求换开头"},
        {"action_type": "其他", "note": "日常问好"},          # 应忽略
        {"action_type": "结案复盘", "note": '{"roi": 2.1}'},  # JSON 留痕应忽略
    ]
    ls = extract_lessons(fs)
    assert any("拒绝" in x for x in ls), ls
    assert any("返点" in x for x in ls), ls
    assert any("内容协作" in x for x in ls) or any("改稿" in x for x in ls), ls
    assert all("日常问好" not in x for x in ls)
    print("  extract_lessons OK:", len(ls), "条")


def test_render_case_md():
    """结案案例渲染：含结案数据/复盘经验/大事记，且未结案单应报错。"""
    r = render_case_md(DEAL, extra_lessons=["测试补充要点X"])
    assert r is not None
    rel, title, content = r
    assert rel == f"deal_cases/{DEAL}_案例.md"
    assert "## 三、结案数据" in content and "## 四、复盘结论" in content
    assert "测试补充要点X" in content
    # 未结案单
    try:
        render_case_md("DC20250001")  # 执行中
        raise AssertionError("执行中商单不应渲染成功")
    except ValueError:
        pass
    print("  render_case_md OK:", rel, len(content), "字")


def test_ingest_incremental_idempotent():
    """增量入库：二次入库应替换旧块而非重复累加；总块数稳定。"""
    rel, title, content = render_case_md(DEAL)
    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM chunk_meta")
        n0 = cur.fetchone()[0]
    r1 = ingest_document_incremental(rel, title, content)
    r2 = ingest_document_incremental(rel, title, content)  # 幂等重入
    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM chunk_meta")
        n1 = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunk_meta WHERE source_file=%s", (rel,))
        dup = cur.fetchone()[0]
    assert r1["ok"] and r2["ok"]
    assert dup == r2["chunks"], f"同文档块数应={r2['chunks']} 实际={dup}"
    assert n1 - n0 == r1["chunks"] - r1["replaced_old"], (n0, n1, r1)
    print(f"  ingest OK: {r2['chunks']} 块(替换旧 {r2['replaced_old']}), 总数 {n0}->{n1}")


if __name__ == "__main__":
    test_extract_lessons()
    test_render_case_md()
    test_ingest_incremental_idempotent()
    print("沉淀流单测 3/3 通过")
