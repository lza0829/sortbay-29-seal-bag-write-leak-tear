from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.bag_boundary import (
    READ_SURFACES,
    WRITE_MOUTHS,
    compute_route_drift,
    decorate_bag,
    domain_event_to_dict,
    gate_view,
    seal_claim,
)
from app.services.projections import overview_data, scan_rows


def resolve_seal_event(conn, event_ref: str):
    row = conn.execute(
        "SELECT * FROM domain_events WHERE event_id=? AND event_type='封袋确认'",
        (event_ref,),
    ).fetchone()
    if row:
        return row

    if str(event_ref).isdigit():
        event_id_by_pk = conn.execute(
            "SELECT * FROM domain_events WHERE id=? AND event_type='封袋确认'",
            (int(event_ref),),
        ).fetchone()
        if event_id_by_pk:
            return event_id_by_pk

        audit = conn.execute(
            "SELECT event_id FROM audit_events WHERE id=? AND event_id IS NOT NULL",
            (int(event_ref),),
        ).fetchone()
        if audit and audit["event_id"]:
            row = conn.execute(
                "SELECT * FROM domain_events WHERE event_id=? AND event_type='封袋确认'",
                (audit["event_id"],),
            ).fetchone()
            if row:
                return row

    raise HTTPException(404, "封口事件不存在或不是封袋确认事件")


def build_seal_replay(conn, event_row) -> dict[str, Any]:
    event = domain_event_to_dict(event_row)
    bag_id = int(event_row["aggregate_id"])
    bag_row = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
    if not bag_row:
        raise HTTPException(404, "封口事件对应的袋不存在")

    bag = decorate_bag(conn, bag_row)
    items = scan_rows(conn, "s.bag_id=?", (bag_id,))
    payload = event.get("payload") or {}
    event_scan_ids = [int(x) for x in payload.get("scan_ids", [])]
    event_barcodes = list(payload.get("barcodes", []))
    current_scan_ids = [int(x["id"]) for x in items]
    current_barcodes = [x["barcode"] for x in items]
    current_by_id = {int(x["id"]): x for x in items}

    route_drift = compute_route_drift(conn, payload, current_by_id)
    for row in route_drift:
        row["seal_event_id"] = event_row["event_id"]

    chute = None
    if bag_row["chute_id"]:
        chute_row = conn.execute(
            """
            SELECT c.*, l.name AS 分拣线名
              FROM chutes c JOIN lines l ON l.id=c.line_id
             WHERE c.id=?
            """,
            (bag_row["chute_id"],),
        ).fetchone()
        if chute_row:
            chute = dict(chute_row)

    event_stream = [
        domain_event_to_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM domain_events
             WHERE aggregate_type='bag' AND aggregate_id=?
             ORDER BY event_version ASC, id ASC
            """,
            (bag_id,),
        ).fetchall()
    ]

    audit_rows = conn.execute(
        """
        SELECT * FROM audit_events
         WHERE event_id=?
            OR (kind='封袋' AND ref_id=?)
            OR (kind='扫描' AND ref_id IN (%s))
         ORDER BY id ASC
        """
        % ",".join("?" for _ in current_scan_ids or [0]),
        (event_row["event_id"], bag_id, *(current_scan_ids or [0])),
    ).fetchall()
    audits = [dict(row) for row in audit_rows]

    route_events = []
    divert_batches = []
    if current_scan_ids:
        marks = ",".join("?" for _ in current_scan_ids)
        route_events = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM route_events WHERE scan_id IN ({marks}) ORDER BY id ASC",
                current_scan_ids,
            ).fetchall()
        ]
        divert_batches = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT DISTINCT db.* FROM divert_batches db
                JOIN scans s ON s.divert_batch_id=db.id
                WHERE s.id IN ({marks})
                ORDER BY db.id ASC
                """,
                current_scan_ids,
            ).fetchall()
        ]

    claim = seal_claim(conn, bag_row)
    unfrozen = [
        {"scan_id": int(row["id"]), "barcode": row["barcode"], "status": row["status"]}
        for row in current_by_id.values()
        if row["status"] != "已封袋"
    ]

    overview = overview_data(conn)
    # Replay must agree with the live seal claim: a member set / count / routing
    # gap is drift, and a claim gap is a 半截 seal that must not be presented as
    # successfully sealed.
    consistency = {
        "event_count": int(payload.get("item_count", len(event_scan_ids)) or 0),
        "bag_sealed_count": int(bag_row["sealed_count"] or 0),
        "current_count": bag["件数"],
        "event_scan_set_matches_bag": set(event_scan_ids) == set(current_scan_ids),
        "event_barcode_set_matches_bag": set(event_barcodes) == set(current_barcodes),
        "missing_scan_ids": sorted(set(event_scan_ids) - set(current_scan_ids)),
        "extra_scan_ids": sorted(set(current_scan_ids) - set(event_scan_ids)),
        "unfrozen_members": unfrozen,
        "route_drift": route_drift,
        "route_drift_count": len(route_drift),
        "routing_snapshot_matches": not route_drift,
        "bag_status_matches_event": bag_row["status"] == "已封袋"
        and bag_row["seal_event_id"] == event_row["event_id"],
        "seal_light": claim["封条已亮"],
        "torn": claim["半截"],
        "gaps": claim["缺口"],
        "no_drift": bag["件数一致"]
        and not route_drift
        and not unfrozen
        and set(event_scan_ids) == set(current_scan_ids)
        and bag_row["status"] == "已封袋"
        and claim["封条已亮"],
    }
    consistency["replay_closed_boundary"] = consistency["no_drift"]

    return {
        "封口事件": event,
        "event": event,
        "策略": payload.get("boundary_policy", "bag_body_write_boundary_closed"),
        "historical_gate": payload.get("boundary"),
        "current_gate": gate_view(bag_row, bag["件数"], claim),
        "写闸": gate_view(bag_row, bag["件数"], claim)["写闸"],
        "write_mouths": WRITE_MOUTHS,
        "read_surfaces": READ_SURFACES,
        "封袋页": bag,
        "bag_page": bag,
        "袋": bag,
        "扫描册": items,
        "scan_book": items,
        "口门在口": chute,
        "chute_door": chute,
        "route_events": route_events,
        "回流批次": divert_batches,
        "总览": overview,
        "overview": overview,
        "审计": audits,
        "audit": audits,
        "事件流": event_stream,
        "event_stream": event_stream,
        "一致性": consistency,
        "consistency": consistency,
        "replay_hint": f"GET /api/封袋/封口事件/{event_row['event_id']}/重放",
    }
