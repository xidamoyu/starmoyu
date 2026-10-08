"""M7b 商单字段变更审批 链路单测（不依赖前端/HTTP）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from server.m7b_service import DealChangeService
from starmoyu import storage

svc = DealChangeService()
BASELINE = {}


def _setup():
    """取一个测试 deal，记录基线 owner。"""
    if "deal_id" in BASELINE:
        return BASELINE
    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute("SELECT deal_id, owner FROM deal WHERE owner IS NOT NULL ORDER BY deal_id LIMIT 1")
        did, owner = cur.fetchone()
    BASELINE.update(deal_id=did, owner=owner)
    return BASELINE


def _restore():
    b = _setup()
    svc.create(b["deal_id"], "owner", str(b["owner"]), change_summary="还原")
    req = svc.list(deal_id=b["deal_id"])[0]
    svc.approve(req["request_id"], "tester")


def test_create_and_approve_owner():
    b = _setup()
    did, old = b["deal_id"], b["owner"]
    req = svc.create(did, "owner", "运营-西莫", change_summary="测试", created_by="test")
    assert req["status"] == "pending"
    assert req["old_value"] == str(old)
    rid = req["request_id"]

    res = svc.approve(rid, "tester", "ok")
    assert res["ok"] and res["deal_updated"]["owner"] == "运营-西莫"

    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute("SELECT owner FROM deal WHERE deal_id=%s", (did,))
        assert cur.fetchone()[0] == "运营-西莫"
        cur.execute("SELECT note FROM deal_followup WHERE deal_id=%s ORDER BY id DESC LIMIT 1", (did,))
        assert "变更批准" in cur.fetchone()[0]

    # 重复批准应被拦截
    try:
        svc.approve(rid, "tester")
        assert False, "重复批准未拦截"
    except ValueError:
        pass
    _restore()


def test_create_invalid_field():
    b = _setup()
    try:
        svc.create(b["deal_id"], "hack_field", "x")
        assert False
    except ValueError as e:
        assert "不允许变更字段" in str(e)


def test_reject_keeps_value():
    b = _setup()
    did, old = b["deal_id"], b["owner"]
    # 先确保 deal 处于基线值
    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute("UPDATE deal SET owner=%s WHERE deal_id=%s", (old, did))
    req = svc.create(did, "owner", "某人", change_summary="test")
    res = svc.reject(req["request_id"], "tester", "不批")
    assert res["status"] == "rejected"
    with storage.pg_connect() as c:
        cur = c.cursor()
        cur.execute("SELECT owner FROM deal WHERE deal_id=%s", (did,))
        assert cur.fetchone()[0] == str(old)  # reject 不改 deal


def test_multi_change_one_deal():
    """一商单可挂多个审批，互不影响。"""
    b = _setup()
    did = b["deal_id"]
    r1 = svc.create(did, "owner", "甲", change_summary="t")
    r2 = svc.create(did, "owner", "乙", change_summary="t")
    assert r1["request_id"] != r2["request_id"]
    svc.approve(r1["request_id"], "t")
    svc.approve(r2["request_id"], "t")
    _restore()


if __name__ == "__main__":
    test_create_and_approve_owner()
    test_create_invalid_field()
    test_reject_keeps_value()
    test_multi_change_one_deal()
    print("m7b 全部断言通过")
