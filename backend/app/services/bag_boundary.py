from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException

from app.services.common import now_str

# 全局唯一采用的口径：袋体写边界全关（架构 A）。
# 封袋确认事件落下后，再入袋、回流拽出、改路由裁决三个写口全部关闭；
# 封袋页/扫描册/口门/总览/审计等读投影仍然开放，并可由同一个 seal_event_id 重放。
#
# 封条灯不是一个可以旁路涂改的徽标：它必须由袋已封 ∪ 写口全关 ∪ 袋成员冻结
# 三者共同推导（见 seal_claim）。任一条件不成立，灯不亮，且写口继续关闭；
# 半截状态只暴露缺口，不允许出现“回执成功但写口仍通”。
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
        "seal_light_rule": "袋已封 ∪ 写口全关 ∪ 袋成员冻结 ∪ 事件可重放",
        "半截处理": "灯灭且写口继续关闭；整笔回滚封袋宣称",
        "allow_add_after_seal": False,
        "allow_divert_after_seal": False,
        "allow_reroute_after_seal": False,
        "writes_when_sealed": {item["key"]: False for item in WRITE_MOUTHS},
        "reads_when_sealed": {item["key"]: True for item in READ_SURFACES},
    }


def _bag_get(bag: Any, key: str, default: Any = None) -> Any:
    if bag is None:
        return default
    if isinstance(bag, dict):
        return bag.get(key, default)
    try:
        return bag[key]
    except (IndexError, KeyError):
        return default


def is_sealed_bag(bag: Any) -> bool:
    """Whether the bag body write boundary is closed.

    Either the row status or the recorded seal event is enough to keep the
    boundary closed. This deliberately errs on the side of refusing writes:
    a half-written seal can never reopen a write mouth.
    """
    if bag is None:
        return False
    return _bag_get(bag, "status") == SEALED_STATUS or bool(_bag_get(bag, "seal_event_id"))


def bag_count(conn, bag_id: int) -> int:
    return int(
        conn.execute("SELECT COUNT(*) AS c FROM scans WHERE bag_id=?", (bag_id,)).fetchone()["c"]
    )


def compute_route_drift(conn, payload: dict[str, Any], current_by_id: dict[int, dict]) -> list[dict]:
    """Compare the seal event routing snapshot against live scans and route rules."""
    drift: list[dict] = []
    for snapshot in payload.get("routing_snapshot", []) or []:
        try:
            scan_id = int(snapshot.get("scan_id"))
        except (TypeError, ValueError):
            continue
        current = current_by_id.get(scan_id)
        if current is None:
            # A member missing from the bag is reported by the membership check,
            # not as routing drift.
            continue

        changes: dict[str, Any] = {}
        if snapshot.get("dest_code") is not None and current.get("dest_code") != snapshot.get("dest_code"):
            changes["dest_code"] = {"sealed": snapshot.get("dest_code"), "current": current.get("dest_code")}
        if snapshot.get("chute_id") is not None and current.get("chute_id") != snapshot.get("chute_id"):
            changes["chute_id"] = {"sealed": snapshot.get("chute_id"), "current": current.get("chute_id")}

        sealed_dest = snapshot.get("dest_code")
        sealed_route_chute = snapshot.get("route_chute_id")
        if sealed_dest and sealed_route_chute is not None:
            rule = conn.execute(
                "SELECT chute_id FROM route_rules WHERE dest_code=?", (sealed_dest,)
            ).fetchone()
            if rule is None:
                changes["route_rule_missing"] = sealed_dest
            elif int(rule["chute_id"]) != int(sealed_route_chute):
                changes["route_table_chute_id"] = {
                    "sealed": sealed_route_chute,
                    "current": rule["chute_id"],
                }

        if changes:
            drift.append(
                {
                    "scan_id": scan_id,
                    "barcode": snapshot.get("barcode"),
                    "changes": changes,
                }
            )
    return drift


def seal_claim(conn, bag: Any) -> dict[str, Any]:
    """Derive the seal claim (the badge/light) from persisted truth.

    The light is on only when *all* of the following hold:

    * the bag row says sealed and carries a seal event id;
    * that seal event exists and its payload can be parsed;
    * the event's member set / count / bag code match the bag right now;
    * ``sealed_count`` equals the live member count;
    * every member is frozen to the sealed scan status;
    * the recorded routing snapshot still matches live scans and route rules.

    Any deviation is a gap (半截). A gap never lights the badge and never
    reopens a write mouth.
    """
    bag_id = _bag_get(bag, "id")
    status = _bag_get(bag, "status")
    event_id = _bag_get(bag, "seal_event_id")
    bag_code = _bag_get(bag, "code")
    sealed_count = int(_bag_get(bag, "sealed_count") or 0)
    current_count = bag_count(conn, bag_id) if bag_id is not None else 0
    boundary_closed = is_sealed_bag(bag)

    base = {
        "policy": POLICY_MODE,
        "sealed": boundary_closed,
        "写边界关闭": boundary_closed,
        "封条已亮": False,
        "seal_light": False,
        "半截": False,
        "torn": False,
        "可重放": False,
        "replayable": False,
        "event_id": event_id,
        "封口事件编号": event_id,
        "sealed_count": sealed_count,
        "封口件数": sealed_count if boundary_closed else None,
        "current_count": current_count,
        "当前件数": current_count,
        "件数一致": (not boundary_closed) or sealed_count == current_count,
        "gaps": [],
        "缺口": [],
    }
    if not boundary_closed:
        return base

    gaps: list[str] = []
    payload: dict[str, Any] = {}
    event_row = None

    if status != SEALED_STATUS:
        gaps.append(f"袋状态为「{status}」而非「{SEALED_STATUS}」")
    if not event_id:
        gaps.append("缺少封口事件编号")
    else:
        event_row = conn.execute(
            "SELECT * FROM domain_events WHERE event_id=? AND event_type='封袋确认'",
            (event_id,),
        ).fetchone()
        if event_row is None:
            gaps.append("封口事件不可重放")
        else:
            try:
                payload = json.loads(event_row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
                gaps.append("封口事件载荷不可解析")

    members = conn.execute(
        "SELECT id, barcode, dest_code, chute_id, status FROM scans WHERE bag_id=? ORDER BY id",
        (bag_id,),
    ).fetchall()
    current_by_id = {int(m["id"]): dict(m) for m in members}
    current_ids = set(current_by_id)

    if event_row is not None:
        # Legacy/migrated seal events may carry only {"legacy": true} with no
        # member snapshot. Compare only what the event actually declares, so an
        # old-but-consistent bag is not falsely reported as torn.
        declared_ids = payload.get("scan_ids")
        if declared_ids is not None:
            event_scan_ids = {int(x) for x in declared_ids}
            if event_scan_ids != current_ids:
                gaps.append("封口事件成员集合与袋内件不一致")
        declared_count = payload.get("item_count")
        if declared_count is not None and int(declared_count) != current_count:
            gaps.append("封口事件件数与袋内当前件数不一致")
        event_bag_code = payload.get("bag_code")
        if event_bag_code not in (None, "") and event_bag_code != bag_code:
            gaps.append("封口事件袋码与袋不一致")

    if sealed_count != current_count:
        gaps.append(f"封口冻结件数 {sealed_count} 与当前件数 {current_count} 不一致")
    unfrozen = [scan_id for scan_id, row in current_by_id.items() if row["status"] != SEALED_STATUS]
    if unfrozen:
        gaps.append(f"袋内仍有 {len(unfrozen)} 件未冻结")
    drift = compute_route_drift(conn, payload, current_by_id) if payload else []
    if drift:
        gaps.append(f"封口后路由目的地被改写 {len(drift)} 件")

    light = not gaps
    base.update(
        {
            "seal_light": light,
            "封条已亮": light,
            "半截": not light,
            "torn": not light,
            "可重放": bool(event_row) and not any("不可重放" in g for g in gaps),
            "replayable": bool(event_row) and not any("不可重放" in g for g in gaps),
            "件数一致": sealed_count == current_count,
            "count_consistent": sealed_count == current_count,
            "event_member_match": not any("成员集合" in g for g in gaps),
            "route_drift_count": len(drift),
            "route_drift": drift,
            "gaps": gaps,
            "缺口": gaps,
        }
    )
    return base


def gate_view(bag: Any, item_count: int | None = None, claim: dict[str, Any] | None = None) -> dict[str, Any]:
    sealed = is_sealed_bag(bag)
    light = bool(claim.get("封条已亮")) if claim else sealed
    gaps = list((claim or {}).get("缺口") or [])
    return {
        "policy": POLICY_MODE,
        "策略": POLICY_LABEL,
        "sealed": sealed,
        "封条已亮": light,
        "半截": sealed and not light,
        "缺口": gaps,
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


def decorate_bag(conn, bag: Any) -> dict[str, Any]:
    data = dict(bag)
    count = bag_count(conn, data["id"])
    claim = seal_claim(conn, data)
    sealed = claim["写边界关闭"]
    light = claim["封条已亮"]
    sealed_count = int(data.get("sealed_count") or 0)
    gates = gate_view(data, count, claim)
    data["件数"] = count
    data["当前件数"] = count
    data["frozen_count"] = sealed_count if sealed else None
    data["封口件数"] = sealed_count if sealed else None
    data["sealed"] = sealed
    data["封袋已封"] = sealed
    data["封条已亮"] = light
    data["seal_light"] = light
    data["半截"] = claim["半截"]
    data["torn"] = claim["torn"]
    data["缺口"] = claim["缺口"]
    data["gaps"] = claim["gaps"]
    data["seal_claim"] = claim
    data["count_frozen"] = sealed
    data["件数冻结"] = sealed
    data["count_consistent"] = claim["件数一致"]
    data["件数一致"] = claim["件数一致"]
    data["add_allowed"] = not sealed
    data["divert_allowed"] = not sealed
    data["reroute_allowed"] = not sealed
    data["再入袋允许"] = not sealed
    data["回流允许"] = not sealed
    data["改路由允许"] = not sealed
    data["policy"] = POLICY_MODE
    data["边界口径"] = POLICY_LABEL
    data["gates"] = gates["gates"]
    data["写闸"] = gates["写闸"]
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
        code = _bag_get(bag, "code", "")
        event_id = _bag_get(bag, "seal_event_id")
        raise HTTPException(
            400,
            f"封袋已封，禁止再入袋：{code} 的封口写边界已全关（封口事件 {event_id}）",
        )
    if _bag_get(bag, "status") != OPEN_STATUS:
        raise HTTPException(400, f"袋状态不可入袋: {_bag_get(bag, 'status')}")


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
        WHERE s.dest_code=? AND (b.status=? OR b.seal_event_id IS NOT NULL)
    """
    params: list[Any] = [dest_code, SEALED_STATUS]
    if target_chute_id is not None:
        sql += " AND COALESCE(s.chute_id, -1) != ?"
        params.append(target_chute_id)
    sql += " GROUP BY b.id ORDER BY b.id DESC LIMIT 1"
    return conn.execute(sql, params).fetchone()
