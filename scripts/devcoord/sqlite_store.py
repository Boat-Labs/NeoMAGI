from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .model import COORD_LABEL, KIND_KEY, CoordError
from .store import CoordRecord

SQLITE_SCHEMA_VERSION = 1
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
    payload_json TEXT NOT NULL DEFAULT '{}'
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
    UNIQUE (milestone_id, event_seq)
);
"""


class SQLiteCoordStore:
    """CoordStore backed by .devcoord/control.db."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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

    def init_store(self) -> None:
        conn = self._connect()
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version == 0:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version={SQLITE_SCHEMA_VERSION}")
            conn.commit()
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
        prefix, local_id = _split_record_id(record_id)

        if prefix == "ms":
            return self._update_milestone(conn, local_id, metadata=metadata, status=status)
        if prefix == "ph":
            return self._update_phase(conn, local_id, metadata=metadata)
        if prefix == "gt":
            return self._update_gate(conn, local_id, metadata=metadata)
        if prefix == "rl":
            return self._update_role(conn, local_id, metadata=metadata)
        if prefix == "mg":
            return self._update_message(conn, int(local_id), metadata=metadata)
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
        conn.commit()
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
        conn.commit()
        return self._load_milestone(conn, milestone_id)

    def _load_milestone(self, conn: sqlite3.Connection, milestone_id: str) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM milestones WHERE milestone_id=?", (milestone_id,)
        ).fetchone()
        if row is None:
            raise CoordError(f"milestone not found: {milestone_id}")
        return _milestone_to_record(row)

    def _list_milestones(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM milestones WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [_milestone_to_record(r) for r in rows]

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
        conn.commit()
        return self._load_phase(conn, milestone_id, phase_id)

    def _update_phase(
        self,
        conn: sqlite3.Connection,
        composite_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CoordRecord:
        milestone_id, phase_id = composite_id.split("|", 1)
        if metadata:
            sets, vals = [], []
            for col in ("phase_state", "last_commit", "closed_at"):
                if col in metadata:
                    sets.append(f"{col}=?")
                    vals.append(metadata[col])
            if sets:
                vals.extend([milestone_id, phase_id])
                conn.execute(
                    f"UPDATE phases SET {', '.join(sets)} WHERE milestone_id=? AND phase_id=?",
                    vals,
                )
                conn.commit()
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
        return _phase_to_record(row)

    def _list_phases(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM phases WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [_phase_to_record(r) for r in rows]

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
        conn.commit()
        return self._load_gate(conn, milestone_id, gate_id)

    def _update_gate(
        self,
        conn: sqlite3.Connection,
        composite_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CoordRecord:
        milestone_id, gate_id = composite_id.split("|", 1)
        if metadata:
            sets, vals = [], []
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
            if sets:
                vals.extend([milestone_id, gate_id])
                conn.execute(
                    f"UPDATE gates SET {', '.join(sets)} WHERE milestone_id=? AND gate_id=?",
                    vals,
                )
                conn.commit()
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
        return _gate_to_record(row)

    def _list_gates(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM gates WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [_gate_to_record(r) for r in rows]

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
        conn.commit()
        return self._load_role(conn, milestone_id, role)

    def _update_role(
        self,
        conn: sqlite3.Connection,
        composite_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CoordRecord:
        milestone_id, role = composite_id.split("|", 1)
        if metadata:
            sets, vals = [], []
            for col in ("agent_state", "action", "current_task", "last_activity", "stale_risk"):
                if col in metadata:
                    sets.append(f"{col}=?")
                    vals.append(metadata[col])
            if sets:
                vals.extend([milestone_id, role])
                conn.execute(
                    f"UPDATE roles SET {', '.join(sets)} WHERE milestone_id=? AND role=?",
                    vals,
                )
                conn.commit()
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
        return _role_to_record(row)

    def _list_roles(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM roles WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [_role_to_record(r) for r in rows]

    # -- message --

    def _create_message(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> CoordRecord:
        milestone_id = meta["milestone"]
        gate_id = meta.get("gate_id", "")
        phase_id = meta.get("phase", "")
        command_name = meta.get("command", "")
        target_role = meta.get("role", "")
        target_commit = meta.get("target_commit", "")
        requires_ack = 1 if meta.get("requires_ack", True) else 0
        effective = 1 if meta.get("effective", False) else 0
        sent_at = meta.get("sent_at", "")
        payload = {
            k: v
            for k, v in meta.items()
            if k
            not in {
                KIND_KEY,
                "milestone",
                "gate_id",
                "phase",
                "command",
                "role",
                "target_commit",
                "requires_ack",
                "effective",
                "sent_at",
                "acked_at",
                "ack_role",
                "ack_commit",
            }
        }
        cur = conn.execute(
            "INSERT INTO messages "
            "(milestone_id, gate_id, phase_id, command_name, target_role, target_commit, "
            " requires_ack, effective, sent_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                milestone_id,
                gate_id,
                phase_id,
                command_name,
                target_role,
                target_commit,
                requires_ack,
                effective,
                sent_at,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
        return self._load_message(conn, cur.lastrowid)

    def _update_message(
        self,
        conn: sqlite3.Connection,
        message_id: int,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CoordRecord:
        if metadata:
            sets, vals = [], []
            if "effective" in metadata:
                sets.append("effective=?")
                vals.append(1 if metadata["effective"] else 0)
            if "acked_at" in metadata:
                sets.append("acked_at=?")
                vals.append(metadata["acked_at"])
            if "ack_role" in metadata:
                sets.append("ack_role=?")
                vals.append(metadata["ack_role"])
            if "ack_commit" in metadata:
                sets.append("ack_commit=?")
                vals.append(metadata["ack_commit"])
            if sets:
                vals.append(message_id)
                conn.execute(
                    f"UPDATE messages SET {', '.join(sets)} WHERE message_id=?", vals
                )
                conn.commit()
        return self._load_message(conn, message_id)

    def _load_message(self, conn: sqlite3.Connection, message_id: int | None) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM messages WHERE message_id=?", (message_id,)
        ).fetchone()
        if row is None:
            raise CoordError(f"message not found: {message_id}")
        return _message_to_record(row)

    def _list_messages(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM messages WHERE milestone_id=?", (milestone,)
        ).fetchall()
        return [_message_to_record(r) for r in rows]

    # -- event --

    def _create_event(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> CoordRecord:
        milestone_id = meta["milestone"]
        event_seq = meta.get("event_seq", 0)
        phase_id = meta.get("phase", "")
        gate_id = meta.get("gate", "")
        role = meta.get("role", "")
        event_type = meta.get("event", "")
        status = meta.get("status", "")
        task = meta.get("task", "")
        target_commit = meta.get("target_commit", "")
        result = meta.get("result", "")
        report_path = meta.get("report_path", "")
        report_commit = meta.get("report_commit", "")
        branch = meta.get("branch", "")
        eta_min = meta.get("eta_min")
        source_message_id_raw = meta.get("source_message_id", "")
        source_message_id = None
        if source_message_id_raw:
            try:
                source_message_id = int(str(source_message_id_raw).split(":")[-1])
            except (ValueError, IndexError):
                pass
        created_at = meta.get("ts", "")
        payload = {
            k: v
            for k, v in meta.items()
            if k
            not in {
                KIND_KEY,
                "milestone",
                "event_seq",
                "phase",
                "gate",
                "role",
                "event",
                "status",
                "task",
                "target_commit",
                "result",
                "report_path",
                "report_commit",
                "branch",
                "eta_min",
                "source_message_id",
                "ts",
            }
        }
        cur = conn.execute(
            "INSERT INTO events "
            "(milestone_id, event_seq, phase_id, gate_id, role, event_type, status, task, "
            " target_commit, result, report_path, report_commit, branch, eta_min, "
            " source_message_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                milestone_id,
                event_seq,
                phase_id,
                gate_id,
                role,
                event_type,
                status,
                task,
                target_commit,
                result,
                report_path,
                report_commit,
                branch,
                eta_min,
                source_message_id,
                json.dumps(payload, ensure_ascii=False),
                created_at,
            ),
        )
        conn.commit()
        return self._load_event(conn, cur.lastrowid)

    def _update_event(
        self,
        conn: sqlite3.Connection,
        event_id: int,
        *,
        status: str | None = None,
    ) -> CoordRecord:
        if status is not None:
            conn.execute("UPDATE events SET status=? WHERE event_id=?", (status, event_id))
            conn.commit()
        return self._load_event(conn, event_id)

    def _load_event(self, conn: sqlite3.Connection, event_id: int | None) -> CoordRecord:
        row = conn.execute(
            "SELECT * FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise CoordError(f"event not found: {event_id}")
        return _event_to_record(row)

    def _list_events(self, conn: sqlite3.Connection, milestone: str) -> list[CoordRecord]:
        rows = conn.execute(
            "SELECT * FROM events WHERE milestone_id=? ORDER BY event_seq",
            (milestone,),
        ).fetchall()
        return [_event_to_record(r) for r in rows]


# -- Record conversion helpers --


def _split_record_id(record_id: str) -> tuple[str, str]:
    parts = record_id.split(":", 1)
    if len(parts) != 2:
        raise CoordError(f"invalid record_id format: {record_id}")
    return parts[0], parts[1]


def _milestone_to_record(row: sqlite3.Row) -> CoordRecord:
    milestone_id = row["milestone_id"]
    return CoordRecord(
        record_id=f"ms:{milestone_id}",
        title=f"Coord milestone {milestone_id}",
        description=f"NeoMAGI devcoord control plane for {milestone_id}.",
        record_type="epic",
        status="open" if row["status"] == "active" else "closed",
        labels=_coord_labels("milestone", milestone_id),
        metadata={
            KIND_KEY: "milestone",
            "milestone": milestone_id,
            "run_date": row["run_date"],
            "schema_version": SQLITE_SCHEMA_VERSION,
        },
        created_at=row["created_at"],
        closed_at=row["closed_at"],
    )


def _phase_to_record(row: sqlite3.Row) -> CoordRecord:
    milestone_id = row["milestone_id"]
    phase_id = row["phase_id"]
    return CoordRecord(
        record_id=f"ph:{milestone_id}|{phase_id}",
        title=f"Coord phase {phase_id}",
        description=f"Coordination phase {phase_id} for {milestone_id}.",
        record_type="task",
        status="open",
        labels=_coord_labels("phase", milestone_id, phase=phase_id),
        metadata={
            KIND_KEY: "phase",
            "milestone": milestone_id,
            "phase": phase_id,
            "phase_state": row["phase_state"],
            "last_commit": row["last_commit"] or "",
        },
    )


def _gate_to_record(row: sqlite3.Row) -> CoordRecord:
    milestone_id = row["milestone_id"]
    gate_id = row["gate_id"]
    return CoordRecord(
        record_id=f"gt:{milestone_id}|{gate_id}",
        title=f"Gate {gate_id}",
        description=f"Gate {gate_id} for phase {row['phase_id']}.",
        record_type="task",
        status="open",
        labels=_coord_labels(
            "gate", milestone_id, phase=row["phase_id"], role=row["allowed_role"]
        ),
        metadata={
            KIND_KEY: "gate",
            "milestone": milestone_id,
            "phase": row["phase_id"],
            "gate_id": gate_id,
            "allowed_role": row["allowed_role"],
            "target_commit": row["target_commit"] or "",
            "gate_state": row["gate_state"],
            "result": row["result"] or "",
            "report_path": row["report_path"] or "",
            "report_commit": row["report_commit"] or "",
            "opened_at": row["opened_at"] or "",
            "closed_at": row["closed_at"] or "",
        },
    )


def _role_to_record(row: sqlite3.Row) -> CoordRecord:
    milestone_id = row["milestone_id"]
    role = row["role"]
    return CoordRecord(
        record_id=f"rl:{milestone_id}|{role}",
        title=f"Agent {role}",
        description=f"Coordination state for {role}.",
        record_type="task",
        status="open",
        labels=_coord_labels("agent", milestone_id, role=role),
        metadata={
            KIND_KEY: "agent",
            "milestone": milestone_id,
            "role": role,
            "agent_state": row["agent_state"],
            "action": row["action"] or "",
            "current_task": row["current_task"] or "",
            "last_activity": row["last_activity"] or "",
            "stale_risk": row["stale_risk"] or "none",
        },
    )


def _message_to_record(row: sqlite3.Row) -> CoordRecord:
    message_id = row["message_id"]
    payload = {}
    if row["payload_json"]:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            pass
    metadata = {
        KIND_KEY: "message",
        "milestone": row["milestone_id"],
        "gate_id": row["gate_id"] or "",
        "phase": row["phase_id"] or "",
        "command": row["command_name"],
        "role": row["target_role"],
        "target_commit": row["target_commit"] or "",
        "requires_ack": bool(row["requires_ack"]),
        "effective": bool(row["effective"]),
        "sent_at": row["sent_at"],
        "acked_at": row["acked_at"] or "",
        "ack_role": row["ack_role"] or "",
        "ack_commit": row["ack_commit"] or "",
    }
    metadata.update(payload)
    return CoordRecord(
        record_id=f"mg:{message_id}",
        title=f"{row['command_name']} -> {row['target_role']}",
        description=payload.get("task", ""),
        record_type="task",
        status="open",
        labels=_coord_labels(
            "message",
            row["milestone_id"],
            phase=row["phase_id"],
            role=row["target_role"],
        ),
        metadata=metadata,
        assignee=row["target_role"],
    )


def _event_to_record(row: sqlite3.Row) -> CoordRecord:
    event_id = row["event_id"]
    payload = {}
    if row["payload_json"]:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            pass
    metadata: dict[str, Any] = {
        KIND_KEY: "event",
        "milestone": row["milestone_id"],
        "event_seq": row["event_seq"],
        "phase": row["phase_id"] or "",
        "gate": row["gate_id"] or "",
        "role": row["role"] or "",
        "event": row["event_type"],
        "status": row["status"] or "",
        "task": row["task"],
        "target_commit": row["target_commit"] or "",
        "result": row["result"] or "",
        "report_path": row["report_path"] or "",
        "report_commit": row["report_commit"] or "",
        "branch": row["branch"] or "",
        "eta_min": row["eta_min"],
        "source_message_id": f"mg:{row['source_message_id']}" if row["source_message_id"] else "",
        "ts": row["created_at"],
    }
    metadata.update(payload)
    return CoordRecord(
        record_id=f"ev:{event_id}",
        title=f"{row['event_type']} {row['role'] or ''} phase {row['phase_id'] or 'na'}",
        description=row["task"],
        record_type="task",
        status="open",
        labels=_coord_labels(
            "event",
            row["milestone_id"],
            phase=row["phase_id"],
            role=row["role"],
        ),
        metadata=metadata,
    )


def _coord_labels(
    kind: str,
    milestone: str,
    *,
    phase: str | None = None,
    role: str | None = None,
) -> tuple[str, ...]:
    labels = [
        COORD_LABEL,
        f"coord-kind-{kind}",
        f"coord-milestone-{milestone}",
    ]
    if phase:
        labels.append(f"coord-phase-{phase}")
    if role:
        labels.append(f"coord-role-{role}")
    return tuple(sorted(labels))
