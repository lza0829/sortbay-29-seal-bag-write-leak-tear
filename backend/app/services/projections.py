from __future__ import annotations

from typing import Any

from app.services.bag_boundary import (
    POLICY_LABEL,
    POLICY_MODE,
    READ_SURFACES,
    WRITE_MOUTHS,
    decorate_bag,
    policy_view,
)

SCAN_SELECT = """
    SELECT s.*,
           c.code AS 口门码,
           c.code AS chute_code,
           c.status AS 口门状态,
           b.code AS 袋码,
           b.code AS bag_code,
           b.status AS 袋状态,
           b.status AS bag_status,
           b.seal_event_id AS 封口事件编号,
           b.seal_event_id AS bag_seal_event_id
      FROM scans s
      LEFT JOIN chutes c ON c.id = s.chute_id
      LEFT JOIN bags b ON b.id = s.bag_id
"""


def decorate_scan(row: Any) -> dict[str, Any]:
    item = dict(row)
    sealed = (
        item.get("status") == "已封袋"
        or item.get("袋状态") == "已封袋"
        or item.get("bag_status") == "已封袋"
        or bool(item.get("封口事件编号") or item.get("bag_seal_event_id"))
    )
    status = item.get("status")
    sealed_event_id = item.get("封口事件编号") or item.get("bag_seal_event_id")
    item["sealed"] = sealed
    item["封袋已封"] = sealed
    item["seal_event_id"] = sealed_event_id
    item["event_id"] = sealed_event_id
    item["路由允许"] = (not sealed) and status in ("待路由", "错分回流")
    item["回流允许"] = (not sealed) and status in ("已路由", "已入袋")
    item["改路由允许"] = (not sealed) and status in ("已路由", "已入袋")
    item["入袋允许"] = (not sealed) and status == "已路由"
    if sealed:
        item["锁定原因"] = "封口事件已落下，袋体写边界全关"
    return item


def scan_rows(conn, where: str = "", params: tuple[Any, ...] = (), limit: int = 500):
    sql = SCAN_SELECT
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY s.id DESC LIMIT ?"
    rows = conn.execute(sql, (*params, limit)).fetchall()
    return [decorate_scan(row) for row in rows]


def chute_rows(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.*, l.name AS line_name, l.name AS 分拣线名,
               COALESCE(SUM(CASE WHEN s.status='已路由' THEN 1 ELSE 0 END), 0) AS 未入袋件数,
               COALESCE(SUM(CASE WHEN s.status='已入袋' THEN 1 ELSE 0 END), 0) AS 集包中件数,
               COALESCE(SUM(CASE WHEN s.status='已封袋' THEN 1 ELSE 0 END), 0) AS 已封袋件数,
               COALESCE(SUM(CASE WHEN s.status IN ('已路由','已入袋') THEN 1 ELSE 0 END), 0) AS 在口件数,
               (
                 SELECT b.code FROM bags b
                  WHERE b.chute_id=c.id AND b.status='集包中'
                  ORDER BY b.id DESC LIMIT 1
               ) AS 集包中袋码,
               (
                 SELECT b.code FROM bags b
                  WHERE b.chute_id=c.id AND b.status='已封袋'
                  ORDER BY b.id DESC LIMIT 1
               ) AS 已封袋袋码,
               (
                 SELECT b.seal_event_id FROM bags b
                  WHERE b.chute_id=c.id AND b.status='已封袋'
                  ORDER BY b.id DESC LIMIT 1
               ) AS 封口事件编号
          FROM chutes c
          JOIN lines l ON l.id=c.line_id
          LEFT JOIN scans s ON s.chute_id=c.id
         GROUP BY c.id
         ORDER BY c.code
        """
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["口门在口"] = {
            "在口件数": item["在口件数"],
            "未入袋件数": item["未入袋件数"],
            "集包中件数": item["集包中件数"],
            "已封袋件数": item["已封袋件数"],
            "集包中袋码": item["集包中袋码"],
            "已封袋袋码": item["已封袋袋码"],
            "封口事件编号": item["封口事件编号"],
        }
        item["event_id"] = item["封口事件编号"]
        result.append(item)
    return result


def overview_data(conn) -> dict[str, Any]:
    shift = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()
    counts = {
        "待路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='待路由'").fetchone()["c"],
        "已路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已路由'").fetchone()["c"],
        "错分回流": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='错分回流'").fetchone()["c"],
        "已入袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已入袋'").fetchone()["c"],
        "已封袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已封袋'").fetchone()["c"],
    }
    chutes = chute_rows(conn)
    bag_rows = conn.execute(
        """
        SELECT b.*, c.code AS 口门码
          FROM bags b JOIN chutes c ON c.id=b.chute_id
         ORDER BY b.id DESC LIMIT 100
        """
    ).fetchall()
    bags = [decorate_bag(conn, row) for row in bag_rows]

    bag_total = conn.execute("SELECT COUNT(*) c FROM bags").fetchone()["c"]
    sealed_bags = conn.execute("SELECT COUNT(*) c FROM bags WHERE status='已封袋'").fetchone()["c"]
    open_bags = conn.execute("SELECT COUNT(*) c FROM bags WHERE status='集包中'").fetchone()["c"]
    frozen_items = conn.execute(
        "SELECT COALESCE(SUM(sealed_count), 0) c FROM bags WHERE status='已封袋'"
    ).fetchone()["c"]
    sealed_scan_items = counts["已封袋"]
    drift_bags = [
        {"id": b["id"], "code": b["code"], "当前件数": b["件数"], "封口件数": b["封口件数"]}
        for b in bags
        if b["sealed"] and not b["件数一致"]
    ]
    events = [
        dict(row)
        for row in conn.execute(
            """
            SELECT event_id, aggregate_id AS bag_id, event_type, event_version, created_at
              FROM domain_events
             WHERE aggregate_type='bag'
             ORDER BY id DESC LIMIT 10
            """
        ).fetchall()
    ]
    reconciliation = {
        "袋数": bag_total,
        "集包中袋数": open_bags,
        "已封袋数": sealed_bags,
        "封口冻结件数": int(frozen_items or 0),
        "已封袋扫描件数": sealed_scan_items,
        "冻结件数一致": int(frozen_items or 0) == sealed_scan_items and not drift_bags,
        "拧花袋数": len(drift_bags),
        "拧花袋": drift_bags,
    }
    return {
        "班次": dict(shift) if shift else None,
        "shift": dict(shift) if shift else None,
        "分布": counts,
        "counts": counts,
        "口门": chutes,
        "chutes": chutes,
        "封袋": bags,
        "bags": bags,
        "最近封口事件": events,
        "recent_seal_events": events,
        "边界策略": policy_view(),
        "policy_view": policy_view(),
        "write_mouths": WRITE_MOUTHS,
        "read_surfaces": READ_SURFACES,
        "对账": reconciliation,
        "reconciliation": reconciliation,
        "policy": POLICY_MODE,
        "边界口径": POLICY_LABEL,
    }
