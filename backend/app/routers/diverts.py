from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import db, row_to_dict, write_db
from app.services.bag_boundary import assert_scan_write_allowed
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services.projections import scan_rows

router = APIRouter(tags=["错分回流"])


class DivertIn(BaseModel):
    扫描编号: int | None = None
    扫描ID: int | None = None
    scan_id: int | None = None
    scanId: int | None = None
    id: int | None = None
    备注: str = ""

    def resolved_scan_id(self) -> int | None:
        return self.扫描编号 or self.扫描ID or self.scan_id or self.scanId or self.id


@router.get("/回流批次")
@router.get("/diverts")
def list_batches():
    with db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM divert_batches ORDER BY id DESC LIMIT 100"
        ).fetchall()]


@router.post("/回流")
@router.post("/错分回流")
@router.post("/回流批次")
@router.post("/diverts")
def create_divert(body: DivertIn):
    scan_ref = body.resolved_scan_id()
    if scan_ref is None:
        raise HTTPException(422, "必须提供扫描编号或 scan_id")
    with write_db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_ref,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")

        # Shared seal gate first: Architecture A closes this mouth after bag seal.
        assert_scan_write_allowed(conn, scan, "错分回流")
        require_unsealed(conn)
        shift = current_shift(conn)

        if scan["status"] not in ("已路由", "已入袋"):
            raise HTTPException(400, "仅已路由或已入袋可标错分回流")
        code = f"回流-{shift['id']}-{now_str().replace(' ', '').replace(':', '')[-8:]}"
        conn.execute(
            "INSERT INTO divert_batches(shift_id, code, status, created_at, note) VALUES (?, ?, '回流中', ?, ?)",
            (shift["id"], code, now_str(), body.备注),
        )
        bid = conn.execute("SELECT id FROM divert_batches WHERE code=?", (code,)).fetchone()["id"]
        cur = conn.execute(
            """
            UPDATE scans
               SET status='错分回流', divert_batch_id=?, bag_id=NULL
             WHERE id=? AND status IN ('已路由','已入袋')
            """,
            (bid, scan_ref),
        )
        if cur.rowcount != 1:
            raise HTTPException(400, "回流并发状态已变化，事务回滚")
        if scan["chute_id"]:
            conn.execute(
                "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
                (scan_ref, scan["chute_id"], "错分回流", now_str(), code),
            )
        audit(conn, "回流", bid, "建回流批次", f"扫描={scan_ref}; {body.备注}")
        rows = scan_rows(conn, "s.id=?", (scan_ref,), limit=1)
        return {
            "批次": row_to_dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (bid,)).fetchone()),
            "扫描": rows[0] if rows else None,
        }

def _surface_router_diverts() -> dict:
    return {"panel": "diverts", "ready": True}

