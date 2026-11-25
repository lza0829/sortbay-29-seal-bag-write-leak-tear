from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.db as db_module
from app.db import db, init_db, write_db
from app.main import app
from app.routers.bags import BagAddIn, _seal_bag_row, add_to_bag
from app.seed import seed_if_empty
from app.services.common import audit as real_audit


@pytest.fixture(scope="session")
def shared_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("sortbay") / "seal-boundary.db"
    db_module.DB_PATH = db_path
    init_db()
    seed_if_empty()
    return db_path


@pytest.fixture
def client(shared_db):
    db_module.DB_PATH = shared_db
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


def _sealed_bag_with_item(client: TestClient) -> dict:
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


def test_policy_is_single_closed_boundary(client):
    policy = client.get("/api/封袋/策略")
    assert policy.status_code == 200
    data = policy.json()
    assert data["mode"] == "bag_body_write_boundary_closed"
    assert data["allow_add_after_seal"] is False
    assert data["allow_divert_after_seal"] is False
    assert data["allow_reroute_after_seal"] is False


def test_unsealed_bag_is_not_falsely_blocked(client):
    scan_id, chute_id, _ = _routed_scan(client)
    bag_id = _open_bag(client, chute_id)
    response = client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id})
    assert response.status_code == 200, response.text
    bag = client.get(f"/api/封袋/{bag_id}").json()
    assert bag["sealed"] is False
    assert bag["add_allowed"] is True
    assert bag["divert_allowed"] is True
    assert bag["reroute_allowed"] is True
    assert bag["件数"] == 1


def test_sealed_bag_blocks_every_bag_body_write(client):
    fixture = _sealed_bag_with_item(client)
    bag_id = fixture["bag_id"]
    sealed_scan = fixture["scan_id"]

    extra_scan, _, _ = _routed_scan(client)

    add = client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": extra_scan})
    assert add.status_code == 400

    divert = client.post("/api/回流批次", json={"扫描编号": sealed_scan})
    assert divert.status_code == 400

    reroute = client.post(f"/api/扫描/{sealed_scan}/改路由", json={"口门编号": 2})
    assert reroute.status_code == 400

    adjudicate = client.post(f"/api/扫描/{sealed_scan}/路由")
    assert adjudicate.status_code == 400

    route_rule = client.post("/api/路由表", json={"目的地码": "110", "口门编号": 2})
    assert route_rule.status_code == 400

    unseal = client.post(f"/api/封袋/{bag_id}/解封", json={"原因": "尝试旁路"})
    assert unseal.status_code == 409
    add_after_denied_unseal = client.post(
        f"/api/封袋/{bag_id}/入袋", json={"扫描编号": extra_scan}
    )
    assert add_after_denied_unseal.status_code == 400

    routes = client.get("/api/路由表").json()
    rule_110 = next(row for row in routes if row["dest_code"] == "110")
    assert rule_110["chute_id"] == fixture["chute_id"]
    assert rule_110["封袋锁定"] is True

    bag = client.get(f"/api/封袋/{bag_id}").json()
    assert bag["件数"] == 1
    assert bag["封口件数"] == 1
    assert bag["件数一致"] is True


def test_seal_event_replays_all_linked_surfaces(client):
    fixture = _sealed_bag_with_item(client)
    event_id = fixture["event_id"]

    response = client.get(f"/api/封袋/封口事件/{event_id}/重放")
    assert response.status_code == 200, response.text
    replay = response.json()

    assert replay["一致性"]["no_drift"] is True
    assert replay["封袋页"]["id"] == fixture["bag_id"]
    assert [row["id"] for row in replay["扫描册"]] == [fixture["scan_id"]]
    assert replay["口门在口"]["id"] == fixture["chute_id"]
    assert replay["总览"]["对账"]["拧花袋数"] == 0
    assert any(row["event_id"] == event_id for row in replay["审计"])
    assert {mouth["key"] for mouth in replay["write_mouths"]} == {
        "add_item",
        "divert",
        "reroute",
    }


def test_out_of_band_route_dirty_write_is_visible_in_replay(client):
    fixture = _sealed_bag_with_item(client)
    event_id = fixture["event_id"]

    with write_db() as conn:
        conn.execute(
            "UPDATE route_rules SET chute_id=? WHERE dest_code=?",
            (fixture["chute_id"] + 1, "110"),
        )

    replay = client.get(f"/api/封袋/封口事件/{event_id}/重放")
    assert replay.status_code == 200, replay.text
    consistency = replay.json()["一致性"]
    assert consistency["routing_snapshot_matches"] is False
    assert consistency["route_drift_count"] >= 1
    assert consistency["no_drift"] is False


def test_bag_scan_chute_overview_and_audit_share_event(client):
    fixture = _sealed_bag_with_item(client)
    event_id = fixture["event_id"]

    bag = client.get("/api/封袋").json()[0]
    assert bag["封口事件编号"] == event_id
    assert bag["sealed"] is True

    scan = next(row for row in client.get("/api/扫描").json() if row["id"] == fixture["scan_id"])
    assert scan["封口事件编号"] == event_id
    assert scan["封袋已封"] is True
    assert scan["回流允许"] is False
    assert scan["改路由允许"] is False

    chute = next(row for row in client.get("/api/口门").json() if row["id"] == fixture["chute_id"])
    assert chute["封口事件编号"] == event_id
    assert chute["已封袋件数"] == 1

    overview = client.get("/api/总览").json()
    assert overview["对账"]["封口冻结件数"] == 1
    assert overview["对账"]["冻结件数一致"] is True

    audits = client.get("/api/审计", params={"event_id": event_id}).json()
    assert len(audits) == 1
    assert "封袋确认" in audits[0]["action"]


def test_failed_seal_write_rolls_back_to_open_consistent_bag(client, monkeypatch):
    scan_id, chute_id, _ = _routed_scan(client)
    bag_id = _open_bag(client, chute_id)
    client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id})

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected seal half-write failure")

    monkeypatch.setattr("app.routers.bags.audit", fail_audit)
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        response = failing_client.post(f"/api/封袋/{bag_id}/封口")
        assert response.status_code == 500

    with db() as conn:
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        event_count = conn.execute("SELECT COUNT(*) c FROM domain_events").fetchone()["c"]
    assert bag["status"] == "集包中"
    assert bag["seal_event_id"] is None
    assert scan["status"] == "已入袋"
    assert event_count == 0

    monkeypatch.setattr("app.routers.bags.audit", real_audit)
    retried = client.post(f"/api/封袋/{bag_id}/封口")
    assert retried.status_code == 200, retried.text
    assert retried.json()["件数一致"] is True


def test_failed_add_write_does_not_drift_bag_or_scan(client, monkeypatch):
    first_scan, chute_id, _ = _routed_scan(client)
    second_scan, _, _ = _routed_scan(client)
    bag_id = _open_bag(client, chute_id)
    client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": first_scan})

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected add half-write failure")

    monkeypatch.setattr("app.routers.bags.audit", fail_audit)
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        response = failing_client.post(
            f"/api/封袋/{bag_id}/入袋", json={"扫描编号": second_scan}
        )
        assert response.status_code == 500

    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (second_scan,)).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)
        ).fetchone()["c"]
    assert scan["status"] == "已路由"
    assert scan["bag_id"] is None
    assert count == 1

    monkeypatch.setattr("app.routers.bags.audit", real_audit)
    retry = client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": second_scan})
    assert retry.status_code == 200, retry.text
    assert client.get(f"/api/封袋/{bag_id}").json()["件数"] == 2


def test_concurrent_seal_and_add_never_allows_post_seal_add(client):
    for _ in range(8):
        first_scan, chute_id, _ = _routed_scan(client)
        extra_scan, _, _ = _routed_scan(client)
        bag_id = _open_bag(client, chute_id)
        client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": first_scan})

        barrier = threading.Barrier(2)

        def seal_job():
            barrier.wait()
            with write_db() as conn:
                return _seal_bag_row(conn, bag_id)

        def add_job():
            barrier.wait()
            return add_to_bag(bag_id, BagAddIn(扫描编号=extra_scan))

        with ThreadPoolExecutor(max_workers=2) as pool:
            seal_future = pool.submit(seal_job)
            add_future = pool.submit(add_job)
            seal_result = seal_future.result(timeout=10)
            add_error = add_future.exception(timeout=10)

        assert seal_result["status"] == "已封袋"
        with db() as conn:
            bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)
            ).fetchone()["c"]

        assert bag["status"] == "已封袋"
        assert bag["sealed_count"] == count
        assert count in (1, 2)
        if count == 1:
            assert isinstance(add_error, HTTPException)
            assert add_error.status_code == 400
        else:
            assert add_error is None
