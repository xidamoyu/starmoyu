"""M4 沉淀流（record_interaction）Service 层 TDD 测试。

跑法（后端不必启动，直连 PG）：
  cd starmoyu && .venv/Scripts/python.exe -m pytest scripts/test_m4_service.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server.m4_service import IngestService, TraitService  # noqa: E402
from starmoyu import storage  # noqa: E402


@pytest.fixture()
def kol_id() -> str:
    """造一个测试达人，测完删。"""
    kid = "KOL-M4TEST"
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM party_traits WHERE party_id=%s", (kid,))
        cur.execute("DELETE FROM kol_profile WHERE kol_id=%s", (kid,))
        cur.execute(
            """INSERT INTO kol_profile (kol_id, kol_name, platform, category, tier, fans_count)
               VALUES (%s,'M4测试达人','抖音','美妆','腰部',500000)""", (kid,))
        conn.commit()
    yield kid
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM party_traits WHERE party_id=%s", (kid,))
        cur.execute("DELETE FROM kol_profile WHERE kol_id=%s", (kid,))
        cur.execute("DELETE FROM ingest_staging WHERE staging_id LIKE 'ST-M4TEST%'")
        conn.commit()


# ============================================================ E: traits

def test_trait_add_defaults_unverified(kol_id):
    """LLM 抽取的 trait 默认 verified=False，不参与检索。"""
    ts = TraitService()
    tid = ts.add("kol", kol_id, "改稿态度", "改稿超2次要加钱",
                 source_quote="改稿超2次要加钱", severity="warning",
                 confidence=0.9, source_type="chat")
    assert tid.startswith("TR")
    rows = ts.list_for_party("kol", kol_id, verified_only=True)
    assert rows == [], "未确认的 trait 不应出现在检索结果"


def test_trait_confirm_makes_visible(kol_id):
    """人工确认后 verified=TRUE，才出现在 list_for_party。"""
    ts = TraitService()
    tid = ts.add("kol", kol_id, "排期习惯", "需提前两周给brief",
                 source_quote="必须提前两周给brief", severity="critical",
                 confidence=0.95, source_type="chat")
    assert ts.confirm(tid) is True
    rows = ts.list_for_party("kol", kol_id, verified_only=True)
    assert len(rows) == 1
    r = rows[0]
    assert r["trait_content"] == "需提前两周给brief"
    assert r["source_quote"] == "必须提前两周给brief"  # 原文引用必须随行返回
    assert r["severity"] == "critical"


def test_trait_severity_order_critical_first(kol_id):
    """查询按 critical > warning > info 排序。"""
    ts = TraitService()
    t1 = ts.add("kol", kol_id, "其他", "一般信息", severity="info", confidence=1.0)
    t2 = ts.add("kol", kol_id, "付款要求", "先款后稿", severity="critical", confidence=1.0)
    for t in (t1, t2):
        ts.confirm(t)
    rows = ts.list_for_party("kol", kol_id)
    assert rows[0]["severity"] == "critical"
    assert rows[-1]["severity"] == "info"


def test_trait_pending_review_lists_unconfirmed(kol_id):
    """pending_review 只列 verified=False，低置信度排前。"""
    ts = TraitService()
    hi = ts.add("kol", kol_id, "沟通偏好", "微信沟通", confidence=0.95, source_type="chat")
    lo = ts.add("kol", kol_id, "内容尺度", "不接受口播", confidence=0.4, source_type="chat")
    try:
        pending = ts.pending_review()
        ids = [p["trait_id"] for p in pending]
        assert lo in ids and hi in ids
        assert ids.index(lo) < ids.index(hi), "低置信度应排前"
    finally:
        pass  # fixture 清理


def test_trait_illegal_category_rejected(kol_id):
    ts = TraitService()
    with pytest.raises(ValueError):
        ts.add("kol", kol_id, "不存在分类", "x")


# ============================================================ D: ingest staging

def test_stage_pending_then_confirm_writes_kol(kol_id):
    """暂存 → 人工确认 → kol_profile 落库 + 状态翻转。"""
    svc = IngestService()
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM kol_profile")
        before = cur.fetchone()[0]

    sid = "ST-M4TEST1"
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM ingest_staging WHERE staging_id=%s", (sid,))
        conn.commit()
    svc.stage("kol", "chat", "她说坑位费1.2万，抖音5万粉", [
        {"field": "kol_name", "value": "M4新达人", "confidence": 0.9},
        {"field": "price_21_60s", "value": 12000, "confidence": 0.5},
    ])
    # 手动指定 staging_id 的替代：直接用返回的 sid 查
    st = svc.get(sid)
    assert st is None  # stage() 生成随机 id，此探针应不存在

    pend = svc.pending()
    assert len(pend) >= 1
    real_sid = pend[0]["staging_id"]
    st = svc.get(real_sid)
    assert st["status"] == "pending"
    assert st["extracted"][1]["confidence"] == 0.5  # 低置信度字段在

    out = svc.confirm(real_sid, {
        "kol_name": "M4新达人", "platform": "抖音", "category": "美妆",
        "tier": "腰部", "fans_count": 50000, "price_21_60s": 12000,
    }, reviewer="admin")
    assert out["kind"] == "kol" and out["written_id"]
    assert out["vectorize_text"].startswith("她说坑位费")  # 原文交上层向量入库

    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM kol_profile")
        after = cur.fetchone()[0]
        cur.execute("SELECT status FROM ingest_staging WHERE staging_id=%s", (real_sid,))
        status = cur.fetchone()[0]
        # 清理
        cur.execute("DELETE FROM kol_profile WHERE kol_name='M4新达人'")
        cur.execute("DELETE FROM ingest_staging WHERE staging_id=%s", (real_sid,))
        conn.commit()
    assert after == before + 1
    assert status == "confirmed"


def test_confirm_twice_rejected(kol_id):
    """已确认的暂存不能再 confirm（防重复入库）。"""
    svc = IngestService()
    sid = svc.stage("kol", "chat", "测试重复确认", [
        {"field": "kol_name", "value": "M4重复达人", "confidence": 0.9}])
    try:
        svc.confirm(sid, {"kol_name": "M4重复达人"}, reviewer="admin")
        with pytest.raises(ValueError):
            svc.confirm(sid, {"kol_name": "M4重复达人"}, reviewer="admin")
    finally:
        with storage.pg_connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM kol_profile WHERE kol_name='M4重复达人'")
            cur.execute("DELETE FROM ingest_staging WHERE staging_id=%s", (sid,))
            conn.commit()


def test_reject_only_pending(kol_id):
    """reject 只对 pending 生效。"""
    svc = IngestService()
    sid = svc.stage("kol", "text", "测试驳回", [{"field": "kol_name", "value": "x"}])
    assert svc.reject(sid) is True
    assert svc.reject(sid) is False  # 已 rejected，不能再拒
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM ingest_staging WHERE staging_id=%s", (sid,))
        conn.commit()
