from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from .model import KIND_KEY, CoordError
from .sqlite_store_support import (
    build_event_insert_values,
    build_message_insert_values,
    event_to_record,
    gate_to_record,
    message_to_record,
    message_update_assignments,
    milestone_to_record,
    phase_to_record,
    role_to_record,
    split_record_id,
)
from .store import CoordRecord

SQLITE_SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS milestones (
    milestone_id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS phases (
    milestone_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    phase_state TEXT NOT NULL DEFAULT 'in_progress',
    last_commit TEXT,
    opened_at TEXT,
    closed_at TEXT,
    PRIMARY KEY (milestone_id, phase_id)
);

CREATE TABLE IF NOT EXISTS gates (
    milestone_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    allowed_role TEXT NOT NULL,
    target_commit TEXT NOT NULL DEFAULT '',
    gate_state TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    report_path TEXT,
    report_commit TEXT,
    opened_at TEXT,
    closed_at TEXT,
    PRIMARY KEY (milestone_id, gate_id)
);

CREATE TABLE IF NOT EXISTS roles (
    milestone_id TEXT NOT NULL,
    role TEXT NOT NULL,
    agent_state TEXT NOT NULL DEFAULT 'idle',
    action TEXT DEFAULT '',
    current_task TEXT DEFAULT '',
    last_activity TEXT DEFAULT '',
    stale_risk TEXT DEFAULT 'none',
    PRIMARY KEY (milestone_id, role)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id TEXT NOT NULL,
    gate_id TEXT,
    phase_id TEXT,
    command_name TEXT NOT NULL,
    target_role TEXT NOT NULL,
    target_commit TEXT,
    requires_ack INTEGER NOT NULL DEFAULT 1,
    effective INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL,
    acked_at TEXT,
    ack_role TEXT,
    ack_commit TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    record_closed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    phase_id TEXT,
    gate_id TEXT,
    role TEXT,
    event_type TEXT NOT NULL,
    status TEXT,
    task TEXT NOT NULL,
    target_commit TEXT,
    result TEXT,
    report_path TEXT,
    report_commit TEXT,
    branch TEXT,
    eta_min INTEGER,
    source_message_id INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    record_closed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (milestone_id, event_seq)
);
"""


class SQLiteCoordStore:
    """CoordStore backed by .devcoord/control.db."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._in_transaction: bool = False

    def _connect(self) -> sqlite3.Connection:
        """Return (or create) a connection to an *existing* control.db.

        Raises ``CoordError`` if the DB file does not exist — only
        ``init_store()`` is allowed to bootstrap from scratch.
        """
        if self._conn is not None:
            return self._conn
        if not self.db_path.exists():
            raise CoordError(
                "control plane not initialized; run "
                "'coord.py init --milestone <name> --roles <roles>' first"
            )
        return self._open()

    def _open(self) -> sqlite3.Connection:
        """Return existing connection, or open a new one (no existence check)."""
        if self._conn is not None:
            return self._conn
        conn = sqlite3.connect(str(self.db_path), timeout=BUSY_TIMEOUT_MS / 1000)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        self._connect()
        was_in_transaction = self._in_transaction
        self._in_transaction = True
        try:
            yield
            if not was_in_transaction:
                self._conn.commit()  # type: ignore[union-attr]
        except BaseException:
            if not was_in_transaction:
                self._conn.rollback()  # type: ignore[union-attr]
            raise
        finally:
            self._in_transaction = was_in_transaction

    def _commit(self) -> None:
        if not self._in_transaction and self._conn is not None:
            self._conn.commit()

    def init_store(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._open()
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version == 0:
            for stmt in _SCHEMA_SQL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            conn.execute(f"PRAGMA user_version={SQLITE_SCHEMA_VERSION}")
            self._commit()
        elif current_version != SQLITE_SCHEMA_VERSION:
            raise CoordError(
                f"incompatible schema version: expected={SQLITE_SCHEMA_VERSION} "
                f"found={current_version}; delete .devcoord/ and re-init"
            )

    def list_records(
        self,
        milestone: str,
        *,
        kind: str | None = None,
    ) -> list[CoordRecord]:
        conn = self._connect()
        results: list[CoordRecord] = []
        if kind is None or kind == "milestone":
            results.extend(self._list_milestones(conn, milestone))
        if kind is None or kind == "phase":
            results.extend(self._list_phases(conn, milestone))
        if kind is None or kind == "gate":
            results.extend(self._list_gates(conn, milestone))
        if kind is None or kind == "agent":
            results.extend(self._list_roles(conn, milestone))
        if kind is None or kind == "message":
            results.extend(self._list_messages(conn, milestone))
        if kind is None or kind == "event":
            results.extend(self._list_events(conn, milestone))
        return results

    def create_record(
        self,
        *,
        title: str,
        record_type: str,
        description: str,
        labels: Sequence[str],
        metadata: dict[str, Any],
        assignee: str | None = None,
        parent_id: str | None = None,
        status: str = "open",
    ) -> CoordRecord:
        conn = self._connect()
        kind = metadata.get(KIND_KEY, "")

        if kind == "milestone":
            return self._create_milestone(conn, metadata, status)
        if kind == "phase":
            return self._create_phase(conn, metadata)
        if kind == "gate":
            return self._create_gate(conn, metadata)
        if kind == "agent":
            return self._create_role(conn, metadata)
        if kind == "message":
            return self._create_message(conn, metadata)
        if kind == "event":
            return self._create_event(conn, metadata)

        raise CoordError(f"unsupported record kind for SQLite: {kind}")

    def update_record(
        self,
        record_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        labels: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        assignee: str | None = None,
        status: str | None = None,
    ) -> CoordRecord:
        conn = self._connect()
        prefix, local_id = split_record_id(record_id)

        if prefix == "ms":
            return self._update_milestone(conn, local_id, metadata=metadata, status=status)
        if prefix == "ph":
            return self._update_phase(conn, local_id, metadata=metadata, status=status)
        if prefix == "gt":
            return self._update_gate(conn, local_id, metadata=metadata, status=status)
        if prefix == "rl":
            return self._update_role(conn, local_id, metadata=metadata, status=status)
        if prefix == "mg":
            return self._update_message(conn, int(local_id), metadata=metadata, status=status)
        if prefix == "ev":
            return self._update_event(conn, int(local_id), status=status)

        raise CoordError(f"cannot update record with unknown prefix: {prefix}")

    # -- milestone --

    def _create_milestone(
        self, conn: sqlite3.Connection, meta: dict[str, Any], status: str
    ) -> CoordRecord:
        milestone_id = meta["milestone"]
        run_date = meta.get("run_date", "")
        created_at = meta.get("created_at", "")
        ms_status = "active" if status == "open" else status
        conn.execute(
            "INSERT OR REPLACE INTO milestones (milestone_id, run_date, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            (milestone_id, run_date, ms_status, created_at),
        )
        self._commit()
        return self._load_milestone(conn, milestone_id)

    def _update_milestone(
        self,
        conn: sqlite3.Connection,
        milestone_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> CoordRecord:
        if metadata:
            sets, vals = [], []
            if "run_date" in metadata:
                sets.append("run_date=?")
                vals.append(metadata["run_date"])
            if "closed_at" in metadata:
                sets.append("closed_at=?")
                vals.append(metadata["closed_at"])
            if sets:
                vals.append(milestone_id)
                conn.execute(
                    f"UPDATE milestones SET {', '.join(sets)} WHERE milestone_id=?", vals
                )
        if status is not None:
            ms_status = "closed" if status == "closed" else "active"
            conn.execute(
                "UPDATE milestones SET status=? WHERE milestone_id=?",
                (ms_status, milestone_id),
            )
        self._commit()
        return self._load_milestone(conn, milestone_id)

    def _load_milestone(self, conn: sqlite3.Connection, milestone_id: str) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM milestones WHERE milestone_id=?", (milestone_id,)
        ).fetchone()
        if row is None:
            raise CoordError(f"milestone not found: {milestone_id}")
        return milestone_to_record(row, SQLITE_SCHEMA_VERSION)

    def _list_milestones(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM milestones WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [milestone_to_record(row, SQLITE_SCHEMA_VERSION) for row in rows]

    # -- phase --

    def _create_phase(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> CoordRecord:
        milestone_id = meta["milestone"]
        phase_id = meta["phase"]
        phase_state = meta.get("phase_state", "in_progress")
        last_commit = meta.get("last_commit", "")
        conn.execute(
            "INSERT OR REPLACE INTO phases (milestone_id, phase_id, phase_state, last_commit) "
            "VALUES (?, ?, ?, ?)",
            (milestone_id, phase_id, phase_state, last_commit),
        )
        self._commit()
        return self._load_phase(conn, milestone_id, phase_id)

    def _update_phase(
        self,
        conn: sqlite3.Connection,
        composite_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> CoordRecord:
        milestone_id, phase_id = composite_id.split("|", 1)
        sets, vals = [], []
        if metadata:
            for col in ("phase_state", "last_commit", "closed_at"):
                if col in metadata:
                    sets.append(f"{col}=?")
                    vals.append(metadata[col])
        if status == "closed":
            sets.append("phase_state=?")
            vals.append("closed")
        if sets:
            vals.extend([milestone_id, phase_id])
            conn.execute(
                f"UPDATE phases SET {', '.join(sets)} WHERE milestone_id=? AND phase_id=?",
                vals,
            )
            self._commit()
        return self._load_phase(conn, milestone_id, phase_id)

    def _load_phase(
        self, conn: sqlite3.Connection, milestone_id: str, phase_id: str
    ) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM phases WHERE milestone_id=? AND phase_id=?",
            (milestone_id, phase_id),
        ).fetchone()
        if row is None:
            raise CoordError(f"phase not found: {milestone_id}/{phase_id}")
        return phase_to_record(row)

    def _list_phases(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM phases WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [phase_to_record(row) for row in rows]

    # -- gate --

    def _create_gate(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> CoordRecord:
        milestone_id = meta["milestone"]
        gate_id = meta["gate_id"]
        phase_id = meta.get("phase", "")
        allowed_role = meta.get("allowed_role", "")
        target_commit = meta.get("target_commit", "")
        gate_state = meta.get("gate_state", "pending")
        conn.execute(
            "INSERT OR REPLACE INTO gates "
            "(milestone_id, gate_id, phase_id, allowed_role, target_commit, gate_state, "
            " result, report_path, report_commit, opened_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                milestone_id,
                gate_id,
                phase_id,
                allowed_role,
                target_commit,
                gate_state,
                meta.get("result", ""),
                meta.get("report_path", ""),
                meta.get("report_commit", ""),
                meta.get("opened_at", ""),
                meta.get("closed_at", ""),
            ),
        )
        self._commit()
        return self._load_gate(conn, milestone_id, gate_id)

    def _update_gate(
        self,
        conn: sqlite3.Connection,
        composite_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> CoordRecord:
        milestone_id, gate_id = composite_id.split("|", 1)
        sets, vals = [], []
        if metadata:
            for col in (
                "gate_state",
                "target_commit",
                "result",
                "report_path",
                "report_commit",
                "opened_at",
                "closed_at",
                "allowed_role",
            ):
                if col in metadata:
                    sets.append(f"{col}=?")
                    vals.append(metadata[col])
        if status == "closed":
            sets.append("gate_state=?")
            vals.append("closed")
        if sets:
            vals.extend([milestone_id, gate_id])
            conn.execute(
                f"UPDATE gates SET {', '.join(sets)} WHERE milestone_id=? AND gate_id=?",
                vals,
            )
            self._commit()
        return self._load_gate(conn, milestone_id, gate_id)

    def _load_gate(
        self, conn: sqlite3.Connection, milestone_id: str, gate_id: str
    ) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM gates WHERE milestone_id=? AND gate_id=?",
            (milestone_id, gate_id),
        ).fetchone()
        if row is None:
            raise CoordError(f"gate not found: {milestone_id}/{gate_id}")
        return gate_to_record(row)

    def _list_gates(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM gates WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [gate_to_record(row) for row in rows]

    # -- role (agent) --

    def _create_role(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> CoordRecord:
        milestone_id = meta["milestone"]
        role = meta["role"]
        conn.execute(
            "INSERT OR REPLACE INTO roles "
            "(milestone_id, role, agent_state, action, current_task, last_activity, stale_risk) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                milestone_id,
                role,
                meta.get("agent_state", "idle"),
                meta.get("action", ""),
                meta.get("current_task", ""),
                meta.get("last_activity", ""),
                meta.get("stale_risk", "none"),
            ),
        )
        self._commit()
        return self._load_role(conn, milestone_id, role)

    def _update_role(
        self,
        conn: sqlite3.Connection,
        composite_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> CoordRecord:
        milestone_id, role = composite_id.split("|", 1)
        sets, vals = [], []
        if metadata:
            for col in ("agent_state", "action", "current_task", "last_activity", "stale_risk"):
                if col in metadata:
                    sets.append(f"{col}=?")
                    vals.append(metadata[col])
        if status == "closed":
            sets.append("agent_state=?")
            vals.append("closed")
        if sets:
            vals.extend([milestone_id, role])
            conn.execute(
                f"UPDATE roles SET {', '.join(sets)} WHERE milestone_id=? AND role=?",
                vals,
            )
            self._commit()
        return self._load_role(conn, milestone_id, role)

    def _load_role(
        self, conn: sqlite3.Connection, milestone_id: str, role: str
    ) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM roles WHERE milestone_id=? AND role=?",
            (milestone_id, role),
        ).fetchone()
        if row is None:
            raise CoordError(f"role not found: {milestone_id}/{role}")
        return role_to_record(row)

    def _list_roles(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM roles WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [role_to_record(row) for row in rows]

    # -- message --

    def _create_message(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> CoordRecord:
        cur = conn.execute(
            "INSERT INTO messages "
            "(milestone_id, gate_id, phase_id, command_name, target_role, target_commit, "
            " requires_ack, effective, sent_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            build_message_insert_values(meta),
        )
        self._commit()
        return self._load_message(conn, cur.lastrowid)

    def _update_message(
        self,
        conn: sqlite3.Connection,
        message_id: int,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> CoordRecord:
        assignments = message_update_assignments(metadata, status)
        if assignments:
            sets = [f"{column}=?" for column, _ in assignments]
            values = [value for _, value in assignments]
            values.append(message_id)
            conn.execute(
                f"UPDATE messages SET {', '.join(sets)} WHERE message_id=?",
                values,
            )
            self._commit()
        return self._load_message(conn, message_id)

    def _load_message(self, conn: sqlite3.Connection, message_id: int | None) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM messages WHERE message_id=?", (message_id,)
        ).fetchone()
        if row is None:
            raise CoordError(f"message not found: {message_id}")
        return message_to_record(row)

    def _list_messages(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM messages WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [message_to_record(row) for row in rows]

    # -- event --

    def _create_event(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> CoordRecord:
        cur = conn.execute(
            "INSERT INTO events "
            "(milestone_id, event_seq, phase_id, gate_id, role, event_type, status, task, "
            " target_commit, result, report_path, report_commit, branch, eta_min, "
            " source_message_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            build_event_insert_values(meta),
        )
        self._commit()
        return self._load_event(conn, cur.lastrowid)

    def _update_event(
        self,
        conn: sqlite3.Connection,
        event_id: int,
        *,
        status: str | None = None,
    ) -> CoordRecord:
        if status == "closed":
            conn.execute(
                "UPDATE events SET record_closed=1 WHERE event_id=?", (event_id,)
            )
            self._commit()
        return self._load_event(conn, event_id)

    def _load_event(self, conn: sqlite3.Connection, event_id: int | None) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise CoordError(f"event not found: {event_id}")
        return event_to_record(row)

    def _list_events(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM events WHERE milestone_id=? ORDER BY event_seq",
            (milestone,),
        ).fetchall()
        return [event_to_record(row) for row in rows]
