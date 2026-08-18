"""SQLite-backed event + issue store with fingerprint dedupe.

Store choice: **SQLite** (documented). Rationale: fully local, zero external
services, byte-reproducible for tests/bench; the schema and access go through this
one repository so a Postgres swap would touch only this file.

Dedupe invariant: events with the same (project, fingerprint) roll up into ONE issue
(count increments, first/last_seen + sample maintained) — never a new issue.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import List, Optional, Tuple

from .grouping import GroupingConfig, DEFAULT_CONFIG, compute_fingerprint, issue_title
from .models import EventEnvelope, Issue, StoreResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint    TEXT NOT NULL,
    project        TEXT NOT NULL,
    level          TEXT NOT NULL,
    type           TEXT,
    culprit        TEXT NOT NULL,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'unresolved',
    first_seen     REAL NOT NULL,
    last_seen      REAL NOT NULL,
    count          INTEGER NOT NULL DEFAULT 0,
    sample_event_id TEXT NOT NULL,
    UNIQUE (project, fingerprint)
);
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    issue_id    INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    project     TEXT NOT NULL,
    level       TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    received_at REAL NOT NULL,
    payload     TEXT NOT NULL,
    FOREIGN KEY (issue_id) REFERENCES issues (id)
);
CREATE INDEX IF NOT EXISTS idx_events_issue ON events (issue_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_issues_project_status ON issues (project, status);
"""

_ISSUE_COLS = (
    "id, fingerprint, project, level, type, culprit, title, status, "
    "first_seen, last_seen, count, sample_event_id"
)


def _row_to_issue(row: sqlite3.Row) -> Issue:
    return Issue(
        id=row["id"],
        fingerprint=row["fingerprint"],
        project=row["project"],
        level=row["level"],
        type=row["type"],
        culprit=row["culprit"],
        title=row["title"],
        status=row["status"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        count=row["count"],
        sample_event_id=row["sample_event_id"],
    )


class Store:
    """Repository over the event/issue tables. All SQL lives here."""

    def __init__(self, path: str = ":memory:", config: GroupingConfig = DEFAULT_CONFIG):
        self.path = path
        self.config = config
        self._lock = threading.Lock()
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- ingest / dedupe -------------------------------------------------
    def store_event(self, event: EventEnvelope, now: Optional[float] = None) -> StoreResult:
        """Insert an event, rolling it up into its issue by fingerprint."""
        now = time.time() if now is None else now
        fp = compute_fingerprint(event, self.config)
        with self._lock:
            # Idempotency: a re-delivered event_id must not double-count.
            dup = self._conn.execute(
                "SELECT issue_id FROM events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if dup is not None:
                return StoreResult(
                    id=event.event_id, issue_id=dup["issue_id"],
                    fingerprint=fp.hash, new_issue=False,
                )
            cur = self._conn.execute(
                "SELECT * FROM issues WHERE project = ? AND fingerprint = ?",
                (event.project, fp.hash),
            )
            existing = cur.fetchone()
            if existing is None:
                cur = self._conn.execute(
                    f"INSERT INTO issues "
                    f"(fingerprint, project, level, type, culprit, title, status, "
                    f" first_seen, last_seen, count, sample_event_id) "
                    f"VALUES (?, ?, ?, ?, ?, ?, 'unresolved', ?, ?, 1, ?)",
                    (
                        fp.hash,
                        event.project,
                        event.level,
                        _event_type(event),
                        fp.culprit,
                        issue_title(event),
                        event.timestamp,
                        event.timestamp,
                        event.event_id,
                    ),
                )
                issue_id = cur.lastrowid
                new_issue = True
            else:
                issue_id = existing["id"]
                new_issue = False
                new_last = max(existing["last_seen"], event.timestamp)
                new_first = min(existing["first_seen"], event.timestamp)
                self._conn.execute(
                    "UPDATE issues SET count = count + 1, last_seen = ?, first_seen = ? "
                    "WHERE id = ?",
                    (new_last, new_first, issue_id),
                )
            self._conn.execute(
                "INSERT OR IGNORE INTO events "
                "(event_id, issue_id, fingerprint, project, level, timestamp, received_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    issue_id,
                    fp.hash,
                    event.project,
                    event.level,
                    event.timestamp,
                    now,
                    event.model_dump_json(),
                ),
            )
            self._conn.commit()
        return StoreResult(
            id=event.event_id, issue_id=issue_id, fingerprint=fp.hash, new_issue=new_issue
        )

    # ---- queries ---------------------------------------------------------
    def get_issue(self, issue_id: int) -> Optional[Issue]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_ISSUE_COLS} FROM issues WHERE id = ?", (issue_id,)
            ).fetchone()
        return _row_to_issue(row) if row else None

    def list_issues(
        self,
        project: Optional[str] = None,
        level: Optional[str] = None,
        status: Optional[str] = None,
        sort: str = "last_seen",
        order: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> List[Issue]:
        sort_col = sort if sort in ("count", "last_seen", "first_seen") else "last_seen"
        order_sql = "DESC" if order.lower() != "asc" else "ASC"
        where, params = [], []
        if project:
            where.append("project = ?")
            params.append(project)
        if level:
            where.append("level = ?")
            params.append(level)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        sql = (
            f"SELECT {_ISSUE_COLS} FROM issues {clause} "
            f"ORDER BY {sort_col} {order_sql} LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_issue(r) for r in rows]

    def get_events_for_issue(self, issue_id: int, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM events WHERE issue_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (issue_id, limit),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def window_event_count(self, issue_id: int, window_seconds: float, now: float) -> int:
        """Count events for an issue within the last `window_seconds` (by received_at)."""
        floor = now - window_seconds
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE issue_id = ? AND received_at >= ?",
                (issue_id, floor),
            ).fetchone()
        return int(row["n"])

    def set_status(self, issue_id: int, status: str) -> Optional[Issue]:
        with self._lock:
            self._conn.execute(
                "UPDATE issues SET status = ? WHERE id = ?", (status, issue_id)
            )
            self._conn.commit()
        return self.get_issue(issue_id)

    def counts(self) -> Tuple[int, int]:
        """(total events, total issues)."""
        with self._lock:
            e = self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
            i = self._conn.execute("SELECT COUNT(*) AS n FROM issues").fetchone()["n"]
        return int(e), int(i)


def _event_type(event: EventEnvelope) -> Optional[str]:
    if event.exception and event.exception.values:
        return event.exception.values[-1].type
    return None
