from __future__ import annotations

"""封袋写口泄漏 · 撕裂夹具（架构 A · 袋体写边界全关）。

现场口径只有一个：封条灯必须由「袋已封 ∪ 集包/改关联拒写 ∪ 袋册只读同命运」重放
推导出来。任一脏写成功即半截，灯灭、写口继续关闭，并且总览/审计/重放必须同命运。
禁止出现“封袋回执成功但袋体写边界仍泄漏”。
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.db as db_module
from app.db import db, init_db, write_db
from app.main import app
from app.seed import seed_if_empty
from app.services.bag_boundary import seal_claim


@pytest.fixture(scope="module")
def shared_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("sortbay-tear") / "seal-tear.db"
    db_module.DB_PATH = db_path
    init_db()
    seed_if_empty()
    return db_path


@pytest.fixture()
def client(shared_db, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", shared_db)
    with db() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.execute("PRAGMA foreign_keys=ON")
    seed_if_empty()
    with TestClient(app) as test_client:
        yield test_client


def _routed_scan(client: TestClient, dest: str = "110") -> tuple[int, int, str]:
    barcode = f"P{uuid.uuid4().hex[:12].upper()}"
    created = client.post("/api/扫描", json={"条码": barcode, "目的地码": dest})
    assert created.status_code == 200, created.text
    scan_id = created.json()["id"]
    routed = client.post(f"/api/扫描/{scan_id}/路由")
    assert routed.status_code == 200, routed.text
    return scan_id, routed.json()["chute_id"], barcode


def _open_bag(client: TestClient, chute_id: int) -> int:
    opened = client.post("/api/封袋/开袋", json={"口门编号": chute_id})
    assert opened.status_code == 200, opened.text
    return opened.json()["id"]


def _sealed(client: TestClient) -> dict:
    scan_id, chute_id, barcode = _routed_scan(client)
    bag_id = _open_bag(client, chute_id)
    added = client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id})
    assert added.status_code == 200, added.text
    sealed = client.post(f"/api/封袋/{bag_id}/封口")
    assert sealed.status_code == 200, sealed.text
    body = sealed.json()
    return {
        "scan_id": scan_id,
        "chute_id": chute_id,
        "barcode": barcode,
        "bag_id": bag_id,
        "event_id": body["封口事件编号"],
        "bag": body,
    }


def _claim(bag_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        return seal_claim(conn, row)


def test_successful_seal_lights_every_surface_from_one_event(client):
    fixture = _sealed(client)
    event_id = fixture["event_id"]

    bag = client.get(f"/api/封袋/{fixture['bag_id']}").json()
    assert bag["封条已亮"] is True
    assert bag["半截"] is False
    assert bag["sealed"] is True
    assert bag["件数一致"] is True
    assert bag["再入袋允许"] is False and bag["回流允许"] is False and bag["改路由允许"] is False

    scans = client.get("/api/扫描").json()
    scan = next(r for r in scans if r["id"] == fixture["scan_id"])
    assert scan["封袋已封"] is True
    assert scan["回流允许"] is False and scan["改路由允许"] is False

    assert client.get("/api/封袋").json()[0]["封条已亮"] is True
    chute = next(c for c in client.get("/api/口门").json() if c["id"] == fixture["chute_id"])
    assert chute["封条已亮"] is True and chute["封口事件编号"] == event_id

    overview = client.get("/api/总览").json()
    assert overview["对账"]["封条已亮袋数"] == 1
    assert overview["对账"]["半截袋数"] == 0
    assert overview["对账"]["冻结件数一致"] is True

    replay = client.get(f"/api/封袋/封口事件/{event_id}/重放").json()
    assert replay["一致性"]["no_drift"] is True
    assert replay["一致性"]["seal_light"] is True
    assert replay["一致性"]["torn"] is False
    assert replay["current_gate"]["封条已亮"] is True

    audits = client.get("/api/审计", params={"event_id": event_id}).json()
    assert len(audits) == 1
    assert "封袋确认" in audits[0]["action"]
    assert all(a["kind"] != "表面" for a in client.get("/api/审计").json())


def test_post_seal_add_still_refused_and_does_not_light_or_tear(client):
    fixture = _sealed(client)
    extra_scan, _, _ = _routed_scan(client)

    add = client.post(f"/api/封袋/{fixture['bag_id']}/入袋", json={"扫描编号": extra_scan})
    assert add.status_code == 400

    bag = client.get(f"/api/封袋/{fixture['bag_id']}").json()
    assert bag["件数"] == 1
    assert bag["封条已亮"] is True
    assert bag["半截"] is False


def test_post_seal_divert_and_reroute_refused(client):
    fixture = _sealed(client)

    divert = client.post("/api/回流批次", json={"扫描编号": fixture["scan_id"]})
    assert divert.status_code == 400

    reroute = client.post(f"/api/扫描/{fixture['scan_id']}/改路由", json={"口门编号": 2})
    assert reroute.status_code == 400

    route_rule = client.post("/api/路由表", json={"目的地码": "110", "口门编号": 2})
    assert route_rule.status_code == 400

    assert client.get(f"/api/封袋/{fixture['bag_id']}").json()["封条已亮"] is True


def test_half_seal_light_on_but_write_mouth_still_open_turns_into_torn(client):
    """半截夹具：徽标被旁路点亮（status/事件在但袋体缺口），写口必须仍全关且灯灭。

    这里直接模拟“回执成功但袋体写边界仍泄漏”的半截：袋状态与封口事件已落，
    但袋里多塞了一件（成员集合超出封口快照）。
    """
    fixture = _sealed(client)
    bag_id = fixture["bag_id"]

    with write_db() as conn:
        extra_id = conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, chute_id, bag_id, scanned_at)
            SELECT ?, dest_code, shift_id, line_id, '已入袋', chute_id, ?, scanned_at
              FROM scans WHERE id=?
            """,
            (f"DIRT{uuid.uuid4().hex[:8].upper()}", bag_id, fixture["scan_id"]),
        ).lastrowid

    claim = _claim(bag_id)
    assert claim["sealed"] is True  # 写边界仍关闭
    assert claim["封条已亮"] is False  # 但灯必须灭
    assert claim["半截"] is True
    assert claim["缺口"]

    # 写口继续全关：半截后不得已封仍脏写。
    add = client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": extra_id})
    assert add.status_code == 400
    divert = client.post("/api/回流批次", json={"扫描编号": fixture["scan_id"]})
    assert divert.status_code == 400
    reroute = client.post(f"/api/扫描/{fixture['scan_id']}/改路由", json={"口门编号": 2})
    assert reroute.status_code == 400
    route_rule = client.post("/api/路由表", json={"目的地码": "110", "口门编号": 2})
    assert route_rule.status_code == 400

    # 二次封袋不能把缺口抹平。
    reseal = client.post(f"/api/封袋/{bag_id}/封口")
    assert reseal.status_code == 400

    # 各读面与重放同命运：都报半截，不得宣称已封可办。
    bag = client.get(f"/api/封袋/{bag_id}").json()
    assert bag["封条已亮"] is False and bag["半截"] is True
    overview = client.get("/api/总览").json()
    assert overview["对账"]["半截袋数"] == 1
    assert overview["对账"]["封条已亮袋数"] == 0
    assert overview["对账"]["冻结件数一致"] is False
    chute = next(
        c for c in client.get("/api/口门").json() if c["id"] == fixture["chute_id"]
    )
    assert chute["封条已亮"] is False and chute["半截"] is True
    replay = client.get(f"/api/封袋/封口事件/{fixture['event_id']}/重放").json()
    assert replay["一致性"]["seal_light"] is False
    assert replay["一致性"]["torn"] is True
    assert replay["一致性"]["no_drift"] is False
    assert replay["一致性"]["extra_scan_ids"] == [extra_id]


def test_out_of_band_reroute_after_seal_shows_as_torn_everywhere(client):
    """封后旁路改路由：灯灭、缺口可核、写口仍全关，审计重放同命运。"""
    fixture = _sealed(client)
    event_id = fixture["event_id"]

    with write_db() as conn:
        conn.execute(
            "UPDATE scans SET dest_code='210' WHERE id=?", (fixture["scan_id"],)
        )

    claim = _claim(fixture["bag_id"])
    assert claim["半截"] is True
    assert claim["route_drift_count"] >= 1

    assert client.get(f"/api/封袋/{fixture['bag_id']}").json()["封条已亮"] is False
    assert client.get("/api/总览").json()["对账"]["半截袋数"] == 1
    replay = client.get(f"/api/审计/封口事件/{event_id}/重放").json()
    assert replay["一致性"]["no_drift"] is False
    assert replay["一致性"]["route_drift_count"] >= 1

    # 写口继续关。
    assert client.post(f"/api/扫描/{fixture['scan_id']}/改路由", json={"口门编号": 2}).status_code == 400


def test_seal_never_lies_when_member_unfrozen(client):
    """扫描被旁路解冻（status 改回已入袋）仍必须报半截，写口不得因此打开。"""
    fixture = _sealed(client)
    with write_db() as conn:
        conn.execute(
            "UPDATE scans SET status='已入袋' WHERE id=?", (fixture["scan_id"],)
        )

    claim = _claim(fixture["bag_id"])
    assert claim["半截"] is True
    assert any("未冻结" in g or "成员集合" in g for g in claim["缺口"])

    assert client.get(f"/api/封袋/{fixture['bag_id']}").json()["封条已亮"] is False
    assert client.post(f"/api/回流批次", json={"扫描编号": fixture["scan_id"]}).status_code == 400
    assert client.post(f"/api/封袋/{fixture['bag_id']}/封口").status_code == 400


def test_seal_and_shift_seal_collision_never_leaves_dirty_member(client):
    """封袋键与集包/封班对撞：要么袋封且件冻结，要么整体回滚，绝不留脏成员。"""
    for _ in range(5):
        scan_id, chute_id, _ = _routed_scan(client)
        extra_scan, _, _ = _routed_scan(client)
        bag_id = _open_bag(client, chute_id)
        assert client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id}).status_code == 200

        barrier = threading.Barrier(2)

        def seal_job():
            barrier.wait()
            return client.post(f"/api/封袋/{bag_id}/封口")

        def add_job():
            barrier.wait()
            return client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": extra_scan})

        with ThreadPoolExecutor(max_workers=2) as pool:
            seal_future = pool.submit(seal_job)
            add_future = pool.submit(add_job)
            seal_resp = seal_future.result(timeout=15)
            add_resp = add_future.result(timeout=15)

        with db() as conn:
            bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
            members = conn.execute(
                "SELECT id, status FROM scans WHERE bag_id=?", (bag_id,)
            ).fetchall()
            count = len(members)

        if seal_resp.status_code == 200:
            assert bag["status"] == "已封袋"
            assert bag["sealed_count"] == count
            assert all(m["status"] == "已封袋" for m in members)
            claim = _claim(bag_id)
            assert claim["封条已亮"] is True and claim["半截"] is False
            if count == 1:
                assert add_resp.status_code == 400
        else:
            # 封袋整体回滚：袋仍集包中，没有封口事件，成员无脏写。
            assert bag["status"] == "集包中"
            assert bag["seal_event_id"] is None
            assert all(m["status"] == "已入袋" for m in members)


def test_failed_seal_claim_half_write_rolls_back_everything(client, monkeypatch):
    """封袋事务中重推封条宣称发现缺口 => 整笔回滚，回执不成功、写口不打开。"""
    scan_id, chute_id, _ = _routed_scan(client)
    bag_id = _open_bag(client, chute_id)
    assert client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id}).status_code == 200

    import app.routers.bags as bags_router

    real_claim = bags_router.seal_claim

    def torn_claim(conn, bag):
        out = real_claim(conn, bag)
        out = dict(out)
        out["封条已亮"] = False
        out["半截"] = True
        out["缺口"] = ["注入：回执与写边界撕裂"]
        return out

    monkeypatch.setattr("app.routers.bags.seal_claim", torn_claim)
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        response = failing_client.post(f"/api/封袋/{bag_id}/封口")
        assert response.status_code == 500

    monkeypatch.setattr("app.routers.bags.seal_claim", real_claim)
    with db() as conn:
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        events = conn.execute("SELECT COUNT(*) c FROM domain_events").fetchone()["c"]
        audits = conn.execute(
            "SELECT COUNT(*) c FROM audit_events WHERE event_id IS NOT NULL"
        ).fetchone()["c"]
    assert bag["status"] == "集包中"
    assert bag["seal_event_id"] is None
    assert scan["status"] == "已入袋"
    assert events == 0
    assert audits == 0

    retried = client.post(f"/api/封袋/{bag_id}/封口")
    assert retried.status_code == 200
    assert retried.json()["封条已亮"] is True


def test_concurrent_torn_repair_attempts_cannot_reopen_write_mouth(client):
    """并发重放/重封尝试：半截袋始终保持写口关闭，灯不会因并发被点亮。"""
    fixture = _sealed(client)
    bag_id = fixture["bag_id"]
    with write_db() as conn:
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, chute_id, bag_id, scanned_at)
            SELECT ?, dest_code, shift_id, line_id, '已入袋', chute_id, ?, scanned_at
              FROM scans WHERE id=?
            """,
            (f"DIRT{uuid.uuid4().hex[:8].upper()}", bag_id, fixture["scan_id"]),
        )

    barrier = threading.Barrier(4)

    def attempt_seal():
        barrier.wait()
        try:
            with write_db() as conn:
                from app.routers.bags import _seal_bag_row

                _seal_bag_row(conn, bag_id)
            return "sealed"
        except HTTPException:
            return "denied"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [pool.submit(attempt_seal) for _ in range(4)]
        outcomes = [f.result(timeout=20) for f in results]

    assert outcomes == ["denied"] * 4
    claim = _claim(bag_id)
    assert claim["封条已亮"] is False and claim["半截"] is True
    assert client.post(f"/api/封袋/{bag_id}/封口").status_code == 400


def test_torn_seal_propagates_to_scan_book_chutes_route_shift_and_audit(client):
    """半截封袋必须让扫描册/口门页/条码目的地/分拣线/班次/审计同命运。"""
    fixture = _sealed(client)
    bag_id = fixture["bag_id"]

    with write_db() as conn:
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, chute_id, bag_id, scanned_at)
            SELECT ?, dest_code, shift_id, line_id, '已入袋', chute_id, ?, scanned_at
              FROM scans WHERE id=?
            """,
            (f"DIRT{uuid.uuid4().hex[:8].upper()}", bag_id, fixture["scan_id"]),
        )

    # 扫描册
    scan = next(
        r for r in client.get("/api/扫描").json() if r["id"] == fixture["scan_id"]
    )
    assert scan["封袋已封"] is True
    assert scan["封条已亮"] is False
    assert scan["半截"] is True
    assert scan["缺口"]
    assert scan["回流允许"] is False and scan["改路由允许"] is False

    # 口门页
    chute = next(
        c for c in client.get("/api/口门").json() if c["id"] == fixture["chute_id"]
    )
    assert chute["封条已亮"] is False and chute["半截"] is True

    # 条码目的地 / 分拣线 / 路由表
    route = next(r for r in client.get("/api/路由表").json() if r["dest_code"] == "110")
    assert route["封袋锁定"] is True
    assert route["封条已亮"] is False and route["半截"] is True

    # 分拣线：已封袋件数仍可核，但不得借此宣称已封可办
    overview = client.get("/api/总览").json()
    assert overview["对账"]["半截袋数"] == 1
    assert overview["对账"]["已封袋扫描件数"] == 1

    # 班次对账
    rec = client.get("/api/班次/对账").json()
    assert rec["封口件数一致"] is False
    assert rec["半截袋"] and rec["半截袋"][0]["id"] == bag_id

    # 审计与重放：同一封口事件可查，且不允许仅改徽标了事
    audits = client.get("/api/审计", params={"event_id": fixture["event_id"]}).json()
    assert any("封袋确认" in a["action"] for a in audits)
    replay = client.get(
        f"/api/审计/封口事件/{fixture['event_id']}/重放"
    ).json()
    assert replay["一致性"]["torn"] is True
    assert replay["封袋页"]["封条已亮"] is False
    assert replay["写闸"]["再入袋"] == "关闭"


def test_loading_route_adjudication_and_divert_flags_share_sealed_event(client):
    """装载→路由落入→改路由/回流 写口与封口事件审计要能被同一事件串起。"""
    fixture = _sealed(client)
    event_id = fixture["event_id"]

    replay = client.get(f"/api/封袋/封口事件/{event_id}/重放").json()
    actions = {row["action"] for row in replay["route_events"]}
    assert "路由落入" in actions

    audit_rows = client.get("/api/审计").json()
    events = {row.get("event_id") for row in audit_rows if row.get("event_id")}
    assert event_id in events

    # 半截回滚并发重放：并发重放不改变写闸（只读、同命运）。
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(client.get, f"/api/封袋/封口事件/{event_id}/重放")
            for _ in range(4)
        ]
    for f in futures:
        body = f.result(timeout=20).json()
        assert body["写闸"]["再入袋"] == "关闭"
        assert body["写闸"]["回流"] == "关闭"
        assert body["写闸"]["改路由"] == "关闭"


def test_legacy_sealed_bag_without_member_snapshot_is_not_falsely_torn(client):
    """迁移库的 legacy 封口事件只带 {"legacy": true}，不得被误判半截。"""
    scan_id, chute_id, _ = _routed_scan(client)
    bag_id = _open_bag(client, chute_id)
    assert client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id}).status_code == 200

    with write_db() as conn:
        conn.execute(
            "INSERT INTO domain_events(event_id, aggregate_type, aggregate_id, event_type, event_version, payload_json, created_at) "
            "VALUES (?, 'bag', ?, '封袋确认', 1, ?, ?)",
            (f"legacy-seal-bag-{bag_id}", bag_id, '{"legacy": true}', "2026-01-01 00:00:00"),
        )
        conn.execute(
            "UPDATE bags SET status='已封袋', seal_event_id=?, sealed_count=1 WHERE id=?",
            (f"legacy-seal-bag-{bag_id}", bag_id),
        )
        conn.execute("UPDATE scans SET status='已封袋' WHERE bag_id=?", (bag_id,))

    claim = _claim(bag_id)
    assert claim["封条已亮"] is True
    assert claim["半截"] is False
    assert claim["缺口"] == []


def test_seal_status_tamper_back_to_open_still_refuses_writes(client):
    """旁路把已封袋状态改回集包中：写口仍须关闭，且二次封袋不得洗白。"""
    fixture = _sealed(client)
    bag_id = fixture["bag_id"]
    with write_db() as conn:
        conn.execute("UPDATE bags SET status='集包中' WHERE id=?", (bag_id,))

    extra, _, _ = _routed_scan(client)
    assert client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": extra}).status_code == 400
    assert client.post("/api/回流批次", json={"扫描编号": fixture["scan_id"]}).status_code == 400
    assert client.post(f"/api/扫描/{fixture['scan_id']}/改路由", json={"口门编号": 2}).status_code == 400
    assert client.post(f"/api/封袋/{bag_id}/封口").status_code == 400

    bag = client.get(f"/api/封袋/{bag_id}").json()
    assert bag["sealed"] is True
    assert bag["封条已亮"] is False and bag["半截"] is True
