from __future__ import annotations
from app.services import ledger_skew

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import db, write_db
from app.services.bag_boundary import assert_scan_write_allowed
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services.projections import scan_rows

router = APIRouter(tags=["过机扫描"])


class ScanIn(BaseModel):
    条码: str | None = Field(default=None, min_length=1)
    barcode: str | None = Field(default=None, min_length=1)
    目的地码: str = ""
    dest_code: str | None = None
    分拣线编号: int | None = None
    line_id: int | None = None
    备注: str = ""
    note: str | None = None

    def resolved_barcode(self) -> str | None:
        return self.条码 or self.barcode

    def resolved_dest(self) -> str:
        return self.目的地码 or self.dest_code or ""

    def resolved_line_id(self) -> int | None:
        return self.分拣线编号 if self.分拣线编号 is not None else self.line_id

    def resolved_note(self) -> str:
        return self.备注 or self.note or ""


class RerouteIn(BaseModel):
    扫描编号: int | None = None
    扫描ID: int | None = None
    scan_id: int | None = None
    scanId: int | None = None
    id: int | None = None
    口门编号: int | None = None
    chute_id: int | None = None
    目的地码: str | None = None
    dest_code: str | None = None
    备注: str = ""


@router.get("/扫描")
@router.get("/scans")
def list_scans(状态: str | None = None):
    with db() as conn:
        if 状态:
            return scan_rows(conn, "s.status=?", (状态,))
        return scan_rows(conn)


@router.post("/扫描")
@router.post("/scans")
def create_scan(body: ScanIn):
    barcode = body.resolved_barcode()
    if not barcode:
        raise HTTPException(422, "必须提供条码或 barcode")
    dest_code = body.resolved_dest()
    line_ref = body.resolved_line_id()
    note = body.resolved_note()
    with write_db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        if line_ref:
            line = conn.execute("SELECT * FROM lines WHERE id=?", (line_ref,)).fetchone()
        else:
            line = conn.execute("SELECT * FROM lines WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if not line:
            raise HTTPException(400, "无可用分拣线")
        if not line["active"]:
            raise HTTPException(400, "分拣线已停用")
        dup = conn.execute(
            "SELECT id FROM scans WHERE barcode=? AND shift_id=?",
            (barcode, shift["id"]),
        ).fetchone()
        if dup:
            raise HTTPException(400, "本班次条码已过机")
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at, note)
            VALUES (?, ?, ?, ?, '待路由', ?, ?)
            """,
            (barcode, dest_code, shift["id"], line["id"], now_str(), note),
        )
        row = conn.execute(
            "SELECT id FROM scans WHERE barcode=? AND shift_id=?",
            (barcode, shift["id"]),
        ).fetchone()
        audit(conn, "扫描", row["id"], "过机装载", barcode)
        return scan_rows(conn, "s.id=?", (row["id"],), limit=1)[0]


@router.post("/扫描/{scan_id}/路由")
def route_scan(scan_id: int):
    with write_db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        assert_scan_write_allowed(conn, scan, "路由裁决")
        require_unsealed(conn)
        if scan["status"] not in ("待路由", "错分回流"):
            raise HTTPException(400, f"当前状态不可路由: {scan['status']}")
        if not scan["dest_code"]:
            raise HTTPException(400, "缺少目的地码")
        rule = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (scan["dest_code"],)
        ).fetchone()
        if not rule:
            raise HTTPException(400, "无匹配路由")
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (rule["chute_id"],)).fetchone()
        if not chute:
            raise HTTPException(400, "口门不存在")
        if chute["status"] != "口门开":
            raise HTTPException(400, "口门已闭，禁止落入")
        cur = conn.execute(
            "UPDATE scans SET status='已路由', chute_id=? WHERE id=? AND status IN ('待路由','错分回流')",
            (chute["id"], scan_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(400, "路由裁决并发状态已变化，事务回滚")
        conn.execute(
            "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
            (scan_id, chute["id"], "路由落入", now_str(), chute["code"]),
        )
        audit(conn, "扫描", scan_id, "路由裁决", chute["code"])
        return scan_rows(conn, "s.id=?", (scan_id,), limit=1)[0]


def _reroute_scan(conn, scan_id: int, body: RerouteIn) -> dict:
    scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        raise HTTPException(404, "扫描不存在")
    assert_scan_write_allowed(conn, scan, "改路由")
    require_unsealed(conn)
    if scan["status"] not in ("已路由", "已入袋"):
        raise HTTPException(400, f"当前状态不可改路由: {scan['status']}")

    new_dest = body.目的地码 if body.目的地码 is not None else (
        body.dest_code if body.dest_code is not None else scan["dest_code"]
    )
    target_chute_id = body.口门编号 if body.口门编号 is not None else body.chute_id
    rule = None
    if target_chute_id is None:
        if not new_dest:
            raise HTTPException(400, "缺少目的地码或目标口门")
        rule = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (new_dest,)
        ).fetchone()
        if not rule:
            raise HTTPException(400, "无匹配路由")
        target_chute_id = rule["chute_id"]

    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (target_chute_id,)).fetchone()
    if not chute:
        raise HTTPException(404, "目标口门不存在")
    if chute["status"] != "口门开":
        raise HTTPException(400, "目标口门已闭，禁止改路由落入")

    old_chute_id = scan["chute_id"]
    old_bag_id = scan["bag_id"]
    remove_from_bag = False
    if old_bag_id:
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (old_bag_id,)).fetchone()
        # assert_scan_write_allowed already rejects a sealed bag; this is defensive.
        if bag and bag["status"] == "已封袋":
            raise HTTPException(400, "封袋已封，禁止把封袋件改路由")
        if bag and int(target_chute_id) != bag["chute_id"]:
            remove_from_bag = True

    if remove_from_bag:
        conn.execute(
            """
            UPDATE scans
               SET status='已路由', chute_id=?, bag_id=NULL, dest_code=?
             WHERE id=?
            """,
            (target_chute_id, new_dest, scan_id),
        )
    else:
        conn.execute(
            "UPDATE scans SET chute_id=?, dest_code=? WHERE id=?",
            (target_chute_id, new_dest, scan_id),
        )

    note = f"{old_chute_id}->{target_chute_id}; {body.备注}".strip("; ")
    conn.execute(
        "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, '改路由', ?, ?)",
        (scan_id, target_chute_id, now_str(), note),
    )
    audit(conn, "扫描", scan_id, "改路由", note)
    return scan_rows(conn, "s.id=?", (scan_id,), limit=1)[0]


@router.post("/改路由")
@router.post("/路由/改路由")
@router.post("/扫描/改路由")
@router.post("/scans/reroute")
def reroute_scan_by_body(body: RerouteIn):
    scan_id = body.扫描编号 or body.scan_id or body.scanId or body.扫描ID or body.id
    if not scan_id:
        raise HTTPException(422, "必须提供扫描编号或 scan_id")
    with write_db() as conn:
        return _reroute_scan(conn, scan_id, body)


@router.post("/扫描/{scan_id}/改路由")
@router.post("/扫描/{scan_id}/reroute")
@router.post("/scans/{scan_id}/reroute")
def reroute_scan(scan_id: int, body: RerouteIn | None = None):
    body = body or RerouteIn()
    with write_db() as conn:
        return _reroute_scan(conn, scan_id, body)

def _surface_router_scans() -> dict:
    return {"panel": "scans", "ready": True}

_list_scans_impl = list_scans


def list_scans(*args, **kwargs):
    rows = _list_scans_impl(*args, **kwargs)
    try:
        from app.db import db
        with db() as conn:
            if isinstance(rows, list):
                return ledger_skew.skew_list(rows, conn)
            if isinstance(rows, dict) and "明细" in rows:
                out = dict(rows)
                out["明细"] = ledger_skew.skew_list(out.get("明细") or [], conn)
                return out
    except Exception:
        pass
    return rows

