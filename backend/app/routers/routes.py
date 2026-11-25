from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import db, row_to_dict, write_db
from app.services.bag_boundary import find_route_rule_seal_lock
from app.services.common import audit

router = APIRouter(tags=["路由表"])


class RouteIn(BaseModel):
    目的地码: str | None = Field(default=None, min_length=1)
    dest_code: str | None = Field(default=None, min_length=1)
    口门编号: int | None = None
    chute_id: int | None = None
    target_chute_id: int | None = None
    备注: str = ""

    def resolved_dest(self) -> str | None:
        return self.目的地码 or self.dest_code

    def resolved_chute_id(self) -> int | None:
        return self.口门编号 if self.口门编号 is not None else (
            self.chute_id if self.chute_id is not None else self.target_chute_id
        )


@router.get("/路由表")
@router.get("/路由")
@router.get("/routes")
def list_routes():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.code AS 口门码, c.title AS 口门标题,
              (
                SELECT COUNT(*) FROM scans s
                  JOIN bags b ON b.id=s.bag_id
                 WHERE s.dest_code=r.dest_code AND b.status='已封袋'
              ) AS 已封袋件数,
              (
                SELECT b.seal_event_id FROM scans s
                  JOIN bags b ON b.id=s.bag_id
                 WHERE s.dest_code=r.dest_code AND b.status='已封袋'
                 ORDER BY b.id DESC LIMIT 1
              ) AS 封口事件编号
            FROM route_rules r JOIN chutes c ON c.id=r.chute_id
            ORDER BY r.dest_code
            """
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["封袋锁定"] = int(item["已封袋件数"] or 0) > 0
            item["reroute_locked"] = item["封袋锁定"]
            item["event_id"] = item["封口事件编号"]
            result.append(item)
        return result


@router.post("/路由表")
@router.post("/路由")
@router.post("/routes")
def upsert_route(body: RouteIn):
    dest_code = body.resolved_dest()
    target_chute_id = body.resolved_chute_id()
    if not dest_code:
        raise HTTPException(422, "必须提供目的地码或 dest_code")
    if target_chute_id is None:
        raise HTTPException(422, "必须提供口门编号或 chute_id")
    with write_db() as conn:
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (target_chute_id,)).fetchone()
        if not chute:
            raise HTTPException(404, "口门不存在")
        exist = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (dest_code,)
        ).fetchone()

        # Block any rule write that would move a sealed item to a different chute.
        # Checking the recorded scan chute also covers direct-DB fixtures without a rule.
        lock = find_route_rule_seal_lock(conn, dest_code, target_chute_id)
        if lock:
            raise HTTPException(
                400,
                "封袋已封，禁止把已封袋件改路由："
                f"目的地 {dest_code} 存在封袋 {lock['bag_code']}，"
                f"封口事件 {lock['seal_event_id']} 已关闭改路由写口；"
                "历史裁决只能由封口事件重放",
            )

        if exist:
            conn.execute(
                "UPDATE route_rules SET chute_id=?, note=? WHERE id=?",
                (target_chute_id, body.备注, exist["id"]),
            )
            rid = exist["id"]
            action = "改路由" if int(exist["chute_id"]) != target_chute_id else "路由备注更新"
        else:
            conn.execute(
                "INSERT INTO route_rules(dest_code, chute_id, note) VALUES (?, ?, ?)",
                (dest_code, target_chute_id, body.备注),
            )
            rid = conn.execute(
                "SELECT id FROM route_rules WHERE dest_code=?", (dest_code,)
            ).fetchone()["id"]
            action = "新建路由"
        audit(conn, "路由表", rid, action, dest_code)
        return row_to_dict(conn.execute("SELECT * FROM route_rules WHERE id=?", (rid,)).fetchone())

def _surface_router_routes() -> dict:
    return {"panel": "routes", "ready": True}

