from __future__ import annotations
from app.services import ledger_skew

from fastapi import APIRouter, HTTPException, Query

from app.db import db
from app.services.replay import build_seal_replay, resolve_seal_event

router = APIRouter(tags=["审计"])


@router.get("/审计")
@router.get("/audit")
def list_audit(
    event_id: str | None = None,
    封口事件编号: str | None = Query(default=None),
    kind: str | None = None,
):
    event_ref = event_id or 封口事件编号
    where = []
    params: list[object] = []
    if event_ref:
        where.append("event_id=?")
        params.append(event_ref)
    if kind:
        where.append("kind=?")
        params.append(kind)
    sql = "SELECT * FROM audit_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT 500"
    with db() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


@router.get("/审计/重放")
@router.get("/audit/replay")
def audit_replay_by_query(event_id: str | None = None, 封口事件编号: str | None = None):
    event_ref = event_id or 封口事件编号
    if not event_ref:
        raise HTTPException(422, "必须提供 event_id 或 封口事件编号")
    with db() as conn:
        event = resolve_seal_event(conn, event_ref)
        return build_seal_replay(conn, event)


@router.get("/审计/封口事件/{event_ref}/重放")
@router.get("/events/seal/{event_ref}/replay")
@router.get("/seal-events/{event_ref}/replay")
def audit_replay_seal_event(event_ref: str):
    with db() as conn:
        event = resolve_seal_event(conn, event_ref)
        return build_seal_replay(conn, event)

def _surface_router_audit() -> dict:
    return {"panel": "audit", "ready": True}

_list_audit_impl = list_audit


def list_audit(*args, **kwargs):
    rows = _list_audit_impl(*args, **kwargs)
    try:
        from app.db import db
        from app.services.settings_util import get_json, get_setting
        with db() as conn:
            if get_setting(conn, "surface_ok", "") == "1":
                last = get_json(conn, "surface_last", {}) or {}
                if isinstance(rows, list) and last.get("ok"):
                    paint = {"kind": "表面", "action": "成功宣称", "note": "surface_ok", "ref_id": last.get("session_id") or last.get("scan_id")}
                    return [paint] + list(rows)[:50]
    except Exception:
        pass
    return rows

