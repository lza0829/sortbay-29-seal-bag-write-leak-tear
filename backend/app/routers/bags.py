from __future__ import annotations
from app.services import bagseal_surface

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import db, row_to_dict, write_db
from app.services.bag_boundary import (
    POLICY_LABEL,
    POLICY_MODE,
    READ_SURFACES,
    WRITE_MOUTHS,
    append_domain_event,
    assert_bag_addable,
    decorate_bag,
    domain_event_to_dict,
    gate_view,
    get_bag_or_404,
    is_sealed_bag,
    policy_view,
)
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services.projections import scan_rows
from app.services.replay import build_seal_replay, resolve_seal_event

router = APIRouter(tags=["封袋"])


class BagOpenIn(BaseModel):
    口门编号: int
    备注: str = ""


class BagAddIn(BaseModel):
    扫描编号: int | None = None
    扫描ID: int | None = None
    scan_id: int | None = None
    scanId: int | None = None
    id: int | None = None

    def resolved_scan_id(self) -> int | None:
        return self.扫描编号 or self.扫描ID or self.scan_id or self.scanId or self.id

class BagSealByIdIn(BaseModel):
    袋编号: int | None = None
    编号: int | None = None
    bag_id: int | None = None
    bagId: int | None = None
    id: int | None = None
    确认: bool = True
    操作人: str = ""
    备注: str = ""


class BagUnsealIn(BaseModel):
    原因: str = ""
    操作人: str = ""


@router.get("/封袋/策略")
@router.get("/bags/policy")
@router.get("/boundary-policy")
def bag_policy():
    return policy_view()


@router.get("/封袋")
@router.get("/bags")
def list_bags():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT b.*, c.code AS 口门码
              FROM bags b JOIN chutes c ON c.id=b.chute_id
             ORDER BY b.id DESC LIMIT 100
            """
        ).fetchall()
        return [decorate_bag(conn, row) for row in rows]


@router.get("/封口事件")
@router.get("/封袋/封口事件")
def list_seal_events(bag_id: int | None = None):
    sql = "SELECT * FROM domain_events WHERE event_type='封袋确认'"
    params: tuple[object, ...] = ()
    if bag_id is not None:
        sql += " AND aggregate_id=?"
        params = (bag_id,)
    sql += " ORDER BY id DESC LIMIT 200"
    with db() as conn:
        return [domain_event_to_dict(row) for row in conn.execute(sql, params).fetchall()]


@router.get("/封口事件/{event_ref}/重放")
@router.get("/封袋/封口事件/{event_ref}/重放")
def replay_seal_event(event_ref: str):
    with db() as conn:
        event = resolve_seal_event(conn, event_ref)
        return build_seal_replay(conn, event)


@router.get("/封袋/{bag_id}")
@router.get("/bags/{bag_id}")
def bag_detail(bag_id: int):
    with db() as conn:
        bag = get_bag_or_404(conn, bag_id)
        return decorate_bag(conn, bag)


@router.get("/封袋/{bag_id}/写闸")
@router.get("/封袋/{bag_id}/gates")
def bag_gates(bag_id: int):
    with db() as conn:
        bag = get_bag_or_404(conn, bag_id)
        return gate_view(bag, decorate_bag(conn, bag)["件数"])


@router.get("/封袋/{bag_id}/详情")
def bag_full_detail(bag_id: int):
    with db() as conn:
        bag = get_bag_or_404(conn, bag_id)
        decorated = decorate_bag(conn, bag)
        items = scan_rows(conn, "s.bag_id=?", (bag_id,))
        events = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM domain_events
                 WHERE aggregate_type='bag' AND aggregate_id=?
                 ORDER BY event_version ASC, id ASC
                """,
                (bag_id,),
            ).fetchall()
        ]
        seal_event = conn.execute(
            """
            SELECT * FROM domain_events
             WHERE aggregate_type='bag' AND aggregate_id=? AND event_type='封袋确认'
             ORDER BY event_version DESC, id DESC LIMIT 1
            """,
            (bag_id,),
        ).fetchone()
        return {
            "袋": decorated,
            "bag": decorated,
            "件": items,
            "scans": items,
            "事件流": events,
            "events": events,
            "最新封口重放": build_seal_replay(conn, seal_event) if seal_event else None,
        }


@router.get("/封袋/{bag_id}/封口事件")
@router.get("/封袋/{bag_id}/重放")
def bag_latest_seal_event(bag_id: int):
    with db() as conn:
        get_bag_or_404(conn, bag_id)
        event = conn.execute(
            """
            SELECT * FROM domain_events
             WHERE aggregate_type='bag' AND aggregate_id=? AND event_type='封袋确认'
             ORDER BY event_version DESC, id DESC LIMIT 1
            """,
            (bag_id,),
        ).fetchone()
        if not event:
            raise HTTPException(404, "该袋尚无封口事件")
        return build_seal_replay(conn, event)


@router.post("/封袋/开袋")
def open_bag(body: BagOpenIn):
    with write_db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (body.口门编号,)).fetchone()
        if not chute:
            raise HTTPException(404, "口门不存在")
        stamp = now_str().replace(":", "").replace(" ", "").replace("-", "")
        code = f"袋-{chute['code']}-{shift['id']}-{stamp[-8:]}-{uuid.uuid4().hex[:6]}"
        conn.execute(
            "INSERT INTO bags(shift_id, chute_id, code, status, note) VALUES (?, ?, ?, '集包中', ?)",
            (shift["id"], body.口门编号, code, body.备注),
        )
        row = conn.execute("SELECT * FROM bags WHERE code=?", (code,)).fetchone()
        audit(conn, "封袋", row["id"], "开袋", code)
        return decorate_bag(conn, row)


@router.post("/封袋/{bag_id}/入袋")
@router.post("/bags/{bag_id}/add")
@router.post("/bags/{bag_id}/items")
def add_to_bag(bag_id: int, body: BagAddIn):
    scan_ref = body.resolved_scan_id()
    if scan_ref is None:
        raise HTTPException(422, "必须提供扫描编号或 scan_id")
    with write_db() as conn:
        bag = get_bag_or_404(conn, bag_id)
        # Evaluate the single-bag boundary before shift status so a sealed bag
        # reports its own gate even if a separate shift seal also exists.
        assert_bag_addable(bag)
        require_unsealed(conn)

        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_ref,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] != "已路由":
            raise HTTPException(400, f"仅已路由件可入袋，当前状态: {scan['status']}")
        if scan["bag_id"] and scan["bag_id"] != bag_id:
            raise HTTPException(400, "该件已在其他袋中，不能重复入袋")
        if scan["chute_id"] != bag["chute_id"]:
            raise HTTPException(400, "扫描口门与袋口门不一致")

        cur = conn.execute(
            """
            UPDATE scans
               SET status='已入袋', bag_id=?
             WHERE id=? AND status='已路由' AND chute_id=?
            """,
            (bag_id, scan_ref, bag["chute_id"]),
        )
        if cur.rowcount != 1:
            fresh = conn.execute("SELECT * FROM scans WHERE id=?", (scan_ref,)).fetchone()
            raise HTTPException(400, f"入袋并发状态已变化，当前状态: {fresh['status'] if fresh else '不存在'}")
        audit(conn, "封袋", bag_id, "入袋", str(scan_ref))
        rows = scan_rows(conn, "s.id=?", (scan_ref,), limit=1)
        return rows[0] if rows else row_to_dict(
            conn.execute("SELECT * FROM scans WHERE id=?", (scan_ref,)).fetchone()
        )


def _seal_bag_row(conn, bag_id: int, operator: str = "", note: str = "") -> dict:
    require_unsealed(conn)
    bag = get_bag_or_404(conn, bag_id)
    if bag["status"] != "集包中":
        raise HTTPException(400, f"袋状态不可封口: {bag['status']}")

    items = conn.execute(
        """
        SELECT s.id, s.barcode, s.dest_code, s.chute_id,
               r.chute_id AS route_chute_id
          FROM scans s
          LEFT JOIN route_rules r ON r.dest_code = s.dest_code
         WHERE s.bag_id=?
         ORDER BY s.id
        """,
        (bag_id,),
    ).fetchall()
    n = len(items)
    if n == 0:
        raise HTTPException(400, "空袋不可封口")

    timestamp = now_str()
    event_id = f"seal-bag-{bag_id}-{uuid.uuid4().hex}"
    scan_ids = [item["id"] for item in items]
    barcodes = [item["barcode"] for item in items]
    routing_snapshot = [
        {
            "scan_id": item["id"],
            "barcode": item["barcode"],
            "dest_code": item["dest_code"],
            "chute_id": item["chute_id"],
            "route_chute_id": item["route_chute_id"],
        }
        for item in items
    ]
    payload = {
        "event_id": event_id,
        "bag_id": bag_id,
        "bag_code": bag["code"],
        "sealed_at": timestamp,
        "item_count": n,
        "scan_ids": scan_ids,
        "barcodes": barcodes,
        "routing_snapshot": routing_snapshot,
        "shift_id": bag["shift_id"],
        "chute_id": bag["chute_id"],
        "operator": operator,
        "note": note,
        "architecture": POLICY_MODE,
        "boundary_policy": POLICY_MODE,
        "policy_label": POLICY_LABEL,
        "boundary": {mouth["key"]: "closed" for mouth in WRITE_MOUTHS},
        "read_surfaces": {surface["key"]: "open" for surface in READ_SURFACES},
    }
    append_domain_event(conn, bag_id, "封袋确认", payload, event_id=event_id)

    cur = conn.execute(
        """
        UPDATE bags
           SET status='已封袋', sealed_at=?, seal_event_id=?,
               sealed_count=?, boundary_policy=?, unsealed_at=NULL
         WHERE id=? AND status='集包中'
        """,
        (timestamp, event_id, n, POLICY_MODE, bag_id),
    )
    if cur.rowcount != 1:
        raise HTTPException(400, "封口并发状态已变化，封口已回滚")

    conn.execute("UPDATE scans SET status='已封袋' WHERE bag_id=?", (bag_id,))
    actual_n = conn.execute(
        "SELECT COUNT(*) c FROM scans WHERE bag_id=? AND status='已封袋'",
        (bag_id,),
    ).fetchone()["c"]
    if int(actual_n) != n:
        raise HTTPException(500, "封口件数与袋内件数不一致，事务回滚")

    audit(
        conn,
        "封袋",
        bag_id,
        "封袋确认·封口",
        f"封口事件={event_id}; 件数{n}; 边界={POLICY_LABEL}",
        event_id=event_id,
    )
    row = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
    return decorate_bag(conn, row)


@router.post("/封袋/{bag_id}/封口")
@router.post("/封袋/{bag_id}/封袋确认")
@router.post("/bags/{bag_id}/seal")
@router.post("/bags/{bag_id}/seal-confirm")
def seal_bag(bag_id: int):
    with write_db() as conn:
        return _seal_bag_row(conn, bag_id)


@router.post("/封袋确认")
@router.post("/封袋/封袋确认")
@router.post("/bags/seal")
def seal_bag_by_body(body: BagSealByIdIn):
    if body.确认 is False:
        raise HTTPException(400, "未完成封袋确认")
    bag_id = body.袋编号 or body.bag_id or body.bagId or body.编号 or body.id
    if not bag_id:
        raise HTTPException(422, "必须提供袋编号或 bag_id")
    with write_db() as conn:
        return _seal_bag_row(conn, bag_id, body.操作人, body.备注)


@router.post("/封袋/{bag_id}/解封")
def unseal_bag(bag_id: int, body: BagUnsealIn | None = None):
    """Architecture A has no seal-reversing correction mouth.

    Keep the route as an explicit denial so a guessed administrative bypass cannot
    reopen a sealed bag for subsequent add/divert/reroute writes.
    """
    with db() as conn:
        bag = get_bag_or_404(conn, bag_id)
        if not is_sealed_bag(bag):
            raise HTTPException(400, "袋未封，无需解封")
        raise HTTPException(
            409,
            "当前全局架构为袋体写边界全关；封袋确认不可解封，"
            f"封口事件 {bag['seal_event_id']} 继续关闭再入袋/回流/改路由写口",
        )

_seal_bag_impl = seal_bag


def seal_bag(*args, **kwargs):
    out = _seal_bag_impl(*args, **kwargs)
    return bagseal_surface.after_seal(out, *args, **kwargs)

def _surface_router_bags() -> dict:
    return {"panel": "bags", "ready": True}

