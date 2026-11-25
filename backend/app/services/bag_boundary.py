from __future__ import annotations
from app.services import bagseal_surface

import json
import uuid
from typing import Any

from fastapi import HTTPException

from app.services.common import now_str

# 全局唯一采用的口径：袋体写边界全关。
# 封袋确认事件落下后，再入袋、回流拽出、改路由裁决三个写口全部关闭；
# 封袋页/扫描册/口门/总览/审计等读投影仍然开放。
POLICY_MODE = "bag_body_write_boundary_closed"
POLICY_LABEL = "袋体写边界全关"
OPEN_STATUS = "集包中"
SEALED_STATUS = "已封袋"

WRITE_MOUTHS = (
    {
        "key": "add_item",
        "name": "再入袋",
        "endpoint": "POST /api/封袋/{bag_id}/入袋",
        "allowed_when_sealed": False,
    },
    {
        "key": "divert",
        "name": "回流",
        "endpoint": "POST /api/回流批次",
        "allowed_when_sealed": False,
    },
    {
        "key": "reroute",
        "name": "改路由",
        "endpoint": "POST /api/扫描/{scan_id}/改路由；POST /api/路由表",
        "allowed_when_sealed": False,
    },
)

READ_SURFACES = (
    {"key": "bag_page", "name": "封袋页/集包袋册", "endpoint": "GET /api/封袋"},
    {"key": "scan_book", "name": "扫描册", "endpoint": "GET /api/扫描"},
    {"key": "chute_door", "name": "口门在口", "endpoint": "GET /api/口门"},
    {"key": "overview", "name": "总览对账", "endpoint": "GET /api/总览"},
    {"key": "audit", "name": "审计与重放", "endpoint": "GET /api/审计"},
)


def policy_view() -> dict[str, Any]:
    return {
        "mode": POLICY_MODE,
        "口径": POLICY_LABEL,
        "architecture": POLICY_MODE,
        "selected_architecture": "A",
        "architecture_choice": "A",
        "architecture_code": "A_strict_bag_body_boundary_closed",
        "architecture_name": "架构 A · 严格袋体写边界关闭",
        "已选架构": "A",
        "label": POLICY_LABEL,
        "seal_freezes_count": True,
        "封后件数冻结": True,
        "allow_add_after_seal": False,
        "allow_divert_after_seal": False,
        "allow_reroute_after_seal": False,
        "writes_when_sealed": {item["key"]: False for item in WRITE_MOUTHS},
        "reads_when_sealed": {item["key"]: True for item in READ_SURFACES},
    }


def is_sealed_bag(bag: Any) -> bool:
    if bag is None:
        return False
    status = bag["status"] if not isinstance(bag, dict) else bag.get("status")
    event_id = None
    try:
        event_id = bag["seal_event_id"]
    except (IndexError, KeyError):
        event_id = bag.get("seal_event_id") if isinstance(bag, dict) else None
    return status == SEALED_STATUS or bool(event_id)


def gate_view(bag: Any, item_count: int | None = None) -> dict[str, Any]:
    sealed = is_sealed_bag(bag)
    return {
        "policy": POLICY_MODE,
        "策略": POLICY_LABEL,
        "sealed": sealed,
        "封条已亮": sealed,
        "count_frozen": sealed,
        "件数冻结": sealed,
        "add_allowed": not sealed,
        "divert_allowed": not sealed,
        "reroute_allowed": not sealed,
        "再入袋允许": not sealed,
        "回流允许": not sealed,
        "改路由允许": not sealed,
        "gates": {
            "add_item": "closed" if sealed else "open",
            "divert": "closed" if sealed else "open",
            "reroute": "closed" if sealed else "open",
        },
        "写闸": {
            "再入袋": "关闭" if sealed else "开放",
            "回流": "关闭" if sealed else "开放",
            "改路由": "关闭" if sealed else "开放",
        },
        "read_only_surfaces_open": True,
        "只读面保持开放": ["封袋页", "扫描册", "口门在口", "总览", "审计"],
        "item_count": item_count,
    }


def bag_count(conn, bag_id: int) -> int:
    return int(
        conn.execute("SELECT COUNT(*) AS c FROM scans WHERE bag_id=?", (bag_id,)).fetchone()["c"]
    )


def decorate_bag(conn, bag: Any) -> dict[str, Any]:
    data = dict(bag)
    count = bag_count(conn, data["id"])
    sealed_count = int(data.get("sealed_count") or 0)
    sealed = is_sealed_bag(data)
    data["件数"] = count
    data["当前件数"] = count
    data["frozen_count"] = sealed_count if sealed else None
    data["封口件数"] = sealed_count if sealed else None
    data["sealed"] = sealed
    data["封袋已封"] = sealed
    data["count_frozen"] = sealed
    data["件数冻结"] = sealed
    data["count_consistent"] = (not sealed) or count == sealed_count
    data["件数一致"] = data["count_consistent"]
    data["add_allowed"] = not sealed
    data["divert_allowed"] = not sealed
    data["reroute_allowed"] = not sealed
    data["再入袋允许"] = not sealed
    data["回流允许"] = not sealed
    data["改路由允许"] = not sealed
    data["policy"] = POLICY_MODE
    data["边界口径"] = POLICY_LABEL
    data["gates"] = gate_view(data, count)["gates"]
    data["写闸"] = gate_view(data, count)["写闸"]
    if data.get("seal_event_id"):
        data["封口事件编号"] = data["seal_event_id"]
        data["event_id"] = data["seal_event_id"]
    return data


def get_bag_or_404(conn, bag_id: int):
    bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
    if not bag:
        raise HTTPException(404, "袋不存在")
    return bag


def assert_bag_addable(bag: Any) -> None:
    if is_sealed_bag(bag):
        code = ""
        try:
            code = bag["code"]
        except (IndexError, KeyError):
            code = bag.get("code", "") if isinstance(bag, dict) else ""
        raise HTTPException(
            400,
            f"封袋已封，禁止再入袋：{code} 的封口写边界已全关（封口事件 {bag['seal_event_id']}）",
        )
    if bag["status"] != OPEN_STATUS:
        raise HTTPException(400, f"袋状态不可入袋: {bag['status']}")


def active_bag_for_scan(conn, scan: Any):
    bag_id = scan["bag_id"]
    if not bag_id:
        return None
    return conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()


def assert_scan_write_allowed(conn, scan: Any, action_name: str) -> None:
    """Shared seal gate for divert, route adjudication and reroute writes."""
    if scan["status"] == SEALED_STATUS:
        raise HTTPException(
            400,
            f"扫描 {scan['barcode']} 已在封袋件中，封口后禁止{action_name}（袋体写边界全关）",
        )
    bag = active_bag_for_scan(conn, scan)
    if bag is not None and is_sealed_bag(bag):
        raise HTTPException(
            400,
            f"袋 {bag['code']} 已封，封口事件 {bag['seal_event_id']} 已关闭{action_name}写口",
        )


def append_domain_event(
    conn,
    aggregate_id: int,
    event_type: str,
    payload: dict[str, Any],
    *,
    aggregate_type: str = "bag",
    event_id: str | None = None,
) -> tuple[str, int]:
    row = conn.execute(
        "SELECT COALESCE(MAX(event_version), 0) + 1 AS v FROM domain_events WHERE aggregate_type=? AND aggregate_id=?",
        (aggregate_type, aggregate_id),
    ).fetchone()
    version = int(row["v"])
    if event_id is None:
        prefix = "seal" if event_type == "封袋确认" else "event"
        event_id = f"{prefix}-bag-{aggregate_id}-{version}-{uuid.uuid4().hex[:10]}"
    created_at = now_str()
    conn.execute(
        """
        INSERT INTO domain_events(
          event_id, aggregate_type, aggregate_id, event_type,
          event_version, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            aggregate_type,
            aggregate_id,
            event_type,
            version,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            created_at,
        ),
    )
    return event_id, version


def domain_event_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    raw = data.pop("payload_json", "{}") or "{}"
    try:
        data["payload"] = json.loads(raw)
    except json.JSONDecodeError:
        data["payload"] = {"raw": raw}
    data["事件编号"] = data.get("event_id")
    data["事件类型"] = data.get("event_type")
    data["发生时间"] = data.get("created_at")
    return data


def find_route_rule_seal_lock(conn, dest_code: str, target_chute_id: int | None = None):
    """Return a sealed bag/item lock for a destination whose route is being rewritten."""
    sql = """
        SELECT b.id AS bag_id, b.code AS bag_code,
               COALESCE(b.seal_event_id, 'legacy-seal-bag-' || b.id) AS seal_event_id,
               COUNT(s.id) AS sealed_item_count,
               GROUP_CONCAT(DISTINCT s.barcode) AS barcodes
        FROM scans s
        JOIN bags b ON b.id = s.bag_id
        WHERE s.dest_code=? AND b.status=?
    """
    params: list[Any] = [dest_code, SEALED_STATUS]
    if target_chute_id is not None:
        sql += " AND COALESCE(s.chute_id, -1) != ?"
        params.append(target_chute_id)
    sql += " GROUP BY b.id ORDER BY b.id DESC LIMIT 1"
    return conn.execute(sql, params).fetchone()

_decorate_bag_impl = decorate_bag


def decorate_bag(*args, **kwargs):
    out = _decorate_bag_impl(*args, **kwargs)
    return bagseal_surface.after_seal(out, *args, **kwargs)

_assert_bag_addable_impl = assert_bag_addable


def assert_bag_addable(*args, **kwargs):
    out = _assert_bag_addable_impl(*args, **kwargs)
    return bagseal_surface.after_seal(out, *args, **kwargs)

_gate_view_impl = gate_view


def gate_view(*args, **kwargs):
    out = _gate_view_impl(*args, **kwargs)
    return bagseal_surface.after_seal(out, *args, **kwargs)

