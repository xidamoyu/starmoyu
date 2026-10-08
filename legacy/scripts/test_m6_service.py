"""M6 TDD：商单钉住 / deal_id 注入 / 路线图 / 结案表单。

运行：.venv/Scripts/python.exe -m pytest scripts/test_m6_service.py -q
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server import m6_service  # noqa: E402
from server.m6_service import (DEAL_ID_RE, close_deal_form, deal_timeline,  # noqa: E402
                               extract_deal_ids, get_pinned, pin_deal, resolve_deal_id,
                               set_stage)
from starmoyu import storage  # noqa: E402


@pytest.fixture()
def conv_id():
    cid = f"conv-test-{int(__import__('time').time())}"
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO conversations (conv_id, user_id, title)
                       VALUES (%s,'u-admin','m6-test') ON CONFLICT (conv_id) DO NOTHING""",
                    (cid,))
        conn.commit()
    yield cid
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM conversations WHERE conv_id=%s", (cid,))
        conn.commit()


@pytest.fixture()
def deal_id():
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT deal_id, result_metrics, stage FROM deal WHERE deal_id='DC20250011'")
        row = cur.fetchone()
    did, old_metrics, old_stage = row
    yield did
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE deal SET result_metrics=%s, stage=%s WHERE deal_id=%s",
                    (json.dumps(old_metrics) if isinstance(old_metrics, dict) else old_metrics,
                     old_stage, did))
        cur.execute("DELETE FROM deal_followup WHERE deal_id=%s AND action_type IN ('结案复盘','手动改阶段')", (did,))
        cur.execute("DELETE FROM party_traits WHERE deal_id=%s AND source_type IN ('screenshot','chat') AND created_at > now() - interval '1 hour'", (did,))
        cur.execute("DELETE FROM ingest_staging WHERE suggested_kind='deal_result' AND created_at > now() - interval '1 hour'")
        conn.commit()


# ---------- 提取
def test_extract_deal_ids():
    assert extract_deal_ids("商单DC20250011和DC20260002都看一下") == ["DC20250011", "DC20260002"]
    assert extract_deal_ids("没有商单号") == []
    assert extract_deal_ids("DC2025001 太短 DC202500111 太长") == []


# ---------- 钉住
def test_pin_and_unpin(conv_id, deal_id):
    assert get_pinned(conv_id) is None
    r = pin_deal(conv_id, deal_id, "u-admin")
    assert r["pinned_deal_id"] == deal_id
    assert get_pinned(conv_id) == deal_id
    with pytest.raises(ValueError):
        pin_deal(conv_id, "DC99999999", "u-admin")  # 不存在的商单
    pin_deal(conv_id, None, "u-admin")
    assert get_pinned(conv_id) is None


# ---------- deal_id 判定（LangGraph configurable）
def test_resolve_deal_id_configurable(conv_id, deal_id):
    assert resolve_deal_id(None) is None            # 图外无 config → None
    assert resolve_deal_id("DC20250001") == "DC20250001"  # 显式优先
    # 模拟 ToolNode 内执行：ensure_config 上下文中可读
    from langchain_core.runnables import ensure_config as _ec
    from contextlib import contextmanager
    import langchain_core
    # langchain_core.runnables.config 的 ensure_config 可与 var config 共存：
    with _conf(pinned=deal_id):
        assert resolve_deal_id(None) == deal_id     # 钉住兜底
        assert resolve_deal_id("DC20250001") == "DC20250001"  # 显式仍优先


@contextmanager
def _conf(pinned):
    """向 langchain-core 的 config var 写入 pinned_deal_id（等价 ToolNode 内环境）。"""
    from langchain_core.runnables.config import var_child_runnable_config
    tok = var_child_runnable_config.set({"configurable": {"pinned_deal_id": pinned}})
    try:
        yield
    finally:
        var_child_runnable_config.reset(tok)


# ---------- 路线图
def test_timeline_structure(deal_id):
    d = deal_timeline(deal_id)
    assert d["deal_id"] == deal_id
    assert d["stage"] in m6_service.STAGES
    assert isinstance(d["followups"], list) and len(d["followups"]) > 0
    assert isinstance(d["traits"], list)
    assert set(m6_service.STAGES) <= {"需求沟通", "提案", "签约", "执行", "结案", "丢单"}
    assert deal_timeline("DC99999999") is None  # 不存在返回 None（API 层转 404）


def test_set_stage_writes_followup(deal_id):
    r = set_stage(deal_id, "签约", "u-admin", note="测试改阶段")
    assert r["stage_to"] == "签约"
    assert r["stage_from"] in m6_service.STAGES
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT stage_to, action_type FROM deal_followup WHERE id=%s",
                    (int(r["followup_id"]),))
        assert cur.fetchone() == ("签约", "手动改阶段")
    with pytest.raises(ValueError):
        set_stage(deal_id, "不存在的阶段", "u-admin")


# ---------- 结案表单（UI 确定性路径）
def test_close_deal_form_full(deal_id):
    specs = [{"kol_id": "K0033", "trait_category": "付款要求",
              "trait_content": "尾款结案后30天", "source_quote": "尾款要结案后30天才结",
              "severity": "warning"}]
    r = close_deal_form(deal_id, {"roi": 2.3, "gmv": 180000},
                        "测试结案一句话", specs, operator="u-admin")
    assert r["ok"] is True
    assert r["deal_result"]["written"]["roi"] == 2.3
    assert len(r["trait_ids"]) == 1
    with storage.pg_connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT deal_id, verified, source_type, trait_content
                       FROM party_traits WHERE trait_id=%s""", (r["trait_ids"][0],))
        tid_d, verified, src, content = cur.fetchone()
    assert (tid_d, verified, src, content) == (deal_id, True, "screenshot", "尾款结案后30天")


def test_close_deal_form_metrics_only(deal_id):
    r = close_deal_form(deal_id, {"exposure": 4200000})
    assert r["ok"] is True
    assert r["trait_ids"] == []
