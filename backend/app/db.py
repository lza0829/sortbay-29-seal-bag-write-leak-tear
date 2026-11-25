from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  name TEXT NOT NULL,
  sealed INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  UNIQUE(business_day, name)
);
CREATE TABLE IF NOT EXISTS chutes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  line_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '口门开',
  soft_cap INTEGER NOT NULL DEFAULT 200,
  UNIQUE(line_id, code)
);
CREATE TABLE IF NOT EXISTS route_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dest_code TEXT NOT NULL UNIQUE,
  chute_id INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  barcode TEXT NOT NULL,
  dest_code TEXT NOT NULL DEFAULT '',
  shift_id INTEGER NOT NULL,
  line_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT '待路由',
  chute_id INTEGER,
  bag_id INTEGER,
  divert_batch_id INTEGER,
  scanned_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(line_id) REFERENCES lines(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS route_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(scan_id) REFERENCES scans(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS divert_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '回流中',
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id)
);
CREATE TABLE IF NOT EXISTS bags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '集包中',
  sealed_at TEXT,
  seal_event_id TEXT,
  sealed_count INTEGER NOT NULL DEFAULT 0,
  boundary_policy TEXT NOT NULL DEFAULT 'bag_body_write_boundary_closed',
  unsealed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS domain_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  aggregate_type TEXT NOT NULL,
  aggregate_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  event_version INTEGER NOT NULL DEFAULT 1,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domain_events_aggregate
  ON domain_events(aggregate_type, aggregate_id, id);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  event_id TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "bags": {
        "seal_event_id": "TEXT",
        "sealed_count": "INTEGER NOT NULL DEFAULT 0",
        "boundary_policy": "TEXT NOT NULL DEFAULT 'bag_body_write_boundary_closed'",
        "unsealed_at": "TEXT",
    },
    "audit_events": {
        "event_id": "TEXT",
    },
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_db():
    """Serialize write transactions and make check-then-write gates atomic."""
    conn = connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in COLUMN_MIGRATIONS.items():
        existing = _existing_columns(conn, table)
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    # Backfill databases created before seal events existed so replay still works.
    legacy = conn.execute(
        """
        SELECT b.*, (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS item_count
        FROM bags b
        WHERE b.status='已封袋' AND (b.seal_event_id IS NULL OR b.seal_event_id='')
        """
    ).fetchall()
    for bag in legacy:
        event_id = f"legacy-seal-bag-{bag['id']}"
        exists = conn.execute(
            "SELECT 1 FROM domain_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO domain_events(
                  event_id, aggregate_type, aggregate_id, event_type,
                  event_version, payload_json, created_at
                ) VALUES (?, 'bag', ?, '封袋确认', 1, ?, ?)
                """,
                (
                    event_id,
                    bag["id"],
                    '{"legacy": true, "boundary": "bag_body_write_boundary_closed"}',
                    bag["sealed_at"] or bag["note"] or "",
                ),
            )
        conn.execute(
            """
            UPDATE bags
               SET seal_event_id=?, sealed_count=CASE WHEN sealed_count=0 THEN ? ELSE sealed_count END
             WHERE id=?
            """,
            (event_id, int(bag["item_count"] or 0), bag["id"]),
        )
        conn.execute(
            "UPDATE audit_events SET event_id=? WHERE kind='封袋' AND ref_id=? AND event_id IS NULL",
            (event_id, bag["id"]),
        )


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
