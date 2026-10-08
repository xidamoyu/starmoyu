"""M5 TDD：结案复盘 / Brief 解析 / 主动简报 / 品牌特质。

运行：.venv/Scripts/python.exe -m pytest scripts/test_m5_service.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server.m5_service import BriefService, BriefingService, DealResultService  # noqa: E402
from server.m4_service import TraitService  # noqa: E402
from starmoyu import storage  # noqa: E402


@pytest.fixture()
def deal_id():
    """取一个测试用商单，跑完恢复原状。"""
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT deal_id, result_metrics, stage FROM deal WHERE stage='执行' LIMIT 1")
        row = cur.fetchone()
    assert row, "没有 stage=执行 的商单可测"
    did, old_metrics, old_stage = row
    yield did
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE deal SET result_metrics=%s, stage=%s WHERE deal_id=%s",
                    (old_metrics, old_stage, did))
        cur.execute("DELETE FROM deal_followup WHERE deal_id=%s AND action_type='结案复盘'", (did,))
        cur.execute("DELETE FROM ingest_staging WHERE staging_id LIKE 'ST%' AND suggested_kind IN ('deal_result','brief') AND created_by='agent'")
        conn.commit()


def test_f1_stage_then_confirm_writes_metrics_and_stage(deal_id):
    svc = DealResultService()
    sid = svc.stage_result(deal_id, "ROI 2.5，GMV 20万，曝光500万", {
        "deal_id": deal_id,
        "metrics": {"roi": 2.5, "gmv": 200000, "exposure": 5000000}})
    # 确认前：deal 不变
    before = svc.get_result(deal_id)
    st = svc.get_staged(sid)
    assert st["status"] == "pending"
    res = svc.confirm_result(sid, reviewer="test")
    after = svc.get_result(deal_id)
    assert after["stage"] == "结案"
    assert after["result_metrics"]["roi"] == 2.5
    assert after["result_metrics"]["gmv"] == 200000
    assert res["followup_id"]


def test_f1_merge_not_overwrite(deal_id):
    """已有指标字段不被覆盖，只 merge 新字段。"""
    svc = DealResultService()
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE deal SET result_metrics=%s::jsonb WHERE deal_id=%s",
                    (json.dumps({"roi": 9.9, "cpm": 12.3}), deal_id))
        conn.commit()
    sid = svc.stage_result(deal_id, "ROI 2.0", {"deal_id": deal_id, "metrics": {"roi": 2.0}})
    svc.confirm_result(sid, reviewer="test")
    d = svc.get_result(deal_id)
    assert d["result_metrics"]["roi"] == 2.0      # 新值覆盖同名
    assert d["result_metrics"]["cpm"] == 12.3      # 旧字段保留


def test_f1_double_confirm_rejected(deal_id):
    svc = DealResultService()
    sid = svc.stage_result(deal_id, "ROI 1.1", {"deal_id": deal_id, "metrics": {"roi": 1.1}})
    svc.confirm_result(sid, reviewer="test")
    with pytest.raises(ValueError):
        svc.confirm_result(sid, reviewer="test")


def test_f2_brief_confirm_creates_proposal_draft():
    svc = BriefService()
    sid = svc.stage_brief("美妆新品，预算8万，要3个腰部达人，下周三前发布", {
        "category": "美妆", "budget": 80000, "kol_count": 3, "schedule": "下周三",
        "requirements": [], "deal_id": None})
    res = svc.confirm_brief(sid, reviewer="test")
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM proposals WHERE proposal_id=%s",
                    (res["proposal_id"],))
        row = cur.fetchone()
        cur.execute("SELECT count(*) FROM proposal_versions WHERE proposal_id=%s",
                    (res["proposal_id"],))
        nver = cur.fetchone()[0]
        cur.execute("DELETE FROM proposal_versions WHERE proposal_id=%s", (res["proposal_id"],))
        cur.execute("DELETE FROM proposals WHERE proposal_id=%s", (res["proposal_id"],))
        conn.commit()
    assert row == ("draft",)
    assert nver == 1


def test_f2_brief_missing_budget_rejected():
    svc = BriefService()
    sid = svc.stage_brief("要投达人", {"category": "美妆", "budget": None})
    with pytest.raises(ValueError):
        svc.confirm_brief(sid, reviewer="test")


def test_f3_briefing_returns_four_sections():
    data = BriefingService().today()
    for key in ("档期临期", "待审批方案", "3天未跟进", "黑名单撞单"):
        assert key in data
        assert isinstance(data[key], list)


def test_f4_brand_traits_roundtrip():
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT brand_id FROM brand LIMIT 1")
        bid = cur.fetchone()[0]
    tid = TraitService().add("brand", bid, "付款要求", "回款周期 60 天",
                             source_quote="尾款要结案后 60 天才结", severity="warning",
                             verified=True, created_by="test")
    rows = TraitService().list_for_party("brand", bid, verified_only=True)
    assert any(r["trait_id"] == tid for r in rows)
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM party_traits WHERE trait_id=%s", (tid,))
        conn.commit()
