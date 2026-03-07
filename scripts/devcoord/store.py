from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .model import (
    COORD_LABEL,
    KIND_KEY,
    CoordError,
    _optional_str,
    _stringify,
    _which,
)


@dataclass
class CoordRecord:
    record_id: str
    title: str
    description: str
    record_type: str
    status: str
    labels: tuple[str, ...]
    metadata: dict[str, Any]
    assignee: str | None = None
    parent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> CoordRecord:
        labels = payload.get("labels") or []
        if isinstance(labels, str):
            labels = [part.strip() for part in labels.split(",") if part.strip()]
        metadata = payload.get("metadata") or payload.get("meta") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(
            record_id=str(payload.get("id") or payload.get("issue_id") or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            record_type=str(payload.get("type") or ""),
            status=str(payload.get("status") or "open"),
            labels=tuple(sorted(str(label) for label in labels)),
            metadata=metadata,
            assignee=_optional_str(payload.get("assignee")),
            parent_id=_optional_str(payload.get("parent_id") or payload.get("parent")),
            created_at=_optional_str(payload.get("created_at")),
            updated_at=_optional_str(payload.get("updated_at")),
            closed_at=_optional_str(payload.get("closed_at")),
        )

    def has_label(self, label: str) -> bool:
        return label in self.labels

    def metadata_str(self, key: str, default: str = "") -> str:
        value = self.metadata.get(key, default)
        return _stringify(value, default)

    def metadata_int(self, key: str, default: int = 0) -> int:
        value = self.metadata.get(key, default)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip():
            return int(value)
        return default

    def metadata_bool(self, key: str, default: bool = False) -> bool:
        value = self.metadata.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)


class CoordStore(Protocol):
    def init_store(self) -> None: ...

    def list_records(
        self,
        milestone: str,
        *,
        kind: str | None = None,
    ) -> list[CoordRecord]: ...

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
    ) -> CoordRecord: ...

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
    ) -> CoordRecord: ...


class BeadsCoordStore:
    """Adapter wrapping beads CLI (bd) as a CoordStore backend."""

    def __init__(
        self,
        beads_dir: Path,
        *,
        bd_bin: str = "bd",
        dolt_bin: str = "dolt",
    ) -> None:
        self.beads_dir = beads_dir
        self.bd_bin = bd_bin
        self.dolt_bin = dolt_bin

    def init_store(self) -> None:
        self._ensure_binary(self.bd_bin, "bd")
        self._ensure_binary(self.dolt_bin, "dolt")
        self.beads_dir.mkdir(parents=True, exist_ok=True)
        metadata_file = self.beads_dir / ".beads" / "metadata.json"
        if metadata_file.exists():
            return
        self._run([self.bd_bin, "init", "--quiet", "--skip-hooks"])

    def list_records(
        self,
        milestone: str,
        *,
        kind: str | None = None,
    ) -> list[CoordRecord]:
        metadata_file = self.beads_dir / ".beads" / "metadata.json"
        if not metadata_file.exists():
            return []
        result = self._run([self.bd_bin, "list", "--all", "--include-infra", "--json"])
        payload = json.loads(result.stdout or "[]")
        if not isinstance(payload, list):
            raise CoordError("bd list --json returned non-list payload")
        all_records = [
            CoordRecord.from_mapping(item) for item in payload if isinstance(item, dict)
        ]
        return [
            r
            for r in all_records
            if r.has_label(COORD_LABEL)
            and r.metadata_str("milestone") == milestone
            and (kind is None or r.metadata_str(KIND_KEY) == kind)
        ]

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
        command = [
            self.bd_bin,
            "create",
            "--silent",
            "--no-inherit-labels",
            "--type",
            record_type,
            "--title",
            title,
            "--description",
            description,
            "--labels",
            ",".join(sorted(set(labels))),
            "--metadata",
            json.dumps(metadata, ensure_ascii=True, sort_keys=True),
        ]
        if assignee:
            command.extend(["--assignee", assignee])
        if parent_id:
            command.extend(["--parent", parent_id])
        record_id = self._run(command).stdout.strip()
        if not record_id:
            raise CoordError(f"bd create returned an empty record id for {title!r}")
        if status != "open":
            self._run([self.bd_bin, "update", record_id, "--status", status])
        return self._reload_record(record_id)

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
        command = [self.bd_bin, "update", record_id]
        if title is not None:
            command.extend(["--title", title])
        if description is not None:
            command.extend(["--description", description])
        if labels is not None:
            for label in sorted(set(labels)):
                command.extend(["--set-labels", label])
        if metadata is not None:
            command.extend(
                ["--metadata", json.dumps(metadata, ensure_ascii=True, sort_keys=True)]
            )
        if assignee is not None:
            command.extend(["--assignee", assignee])
        if status is not None:
            command.extend(["--status", status])
        self._run(command)
        return self._reload_record(record_id)

    def _reload_record(self, record_id: str) -> CoordRecord:
        result = self._run([self.bd_bin, "list", "--all", "--include-infra", "--json"])
        payload = json.loads(result.stdout or "[]")
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    rid = str(item.get("id") or item.get("issue_id") or "")
                    if rid == record_id:
                        return CoordRecord.from_mapping(item)
        raise CoordError(f"record {record_id} not found after write")

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                cwd=self.beads_dir,
                capture_output=True,
                check=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CoordError(
                f"missing binary while running {' '.join(command)}: {exc}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            stdout = exc.stdout.strip()
            message = stderr or stdout or f"command failed: {' '.join(command)}"
            raise CoordError(message) from exc

    @staticmethod
    def _ensure_binary(binary: str, label: str) -> None:
        if _which(binary) is None:
            raise CoordError(
                f"{label} binary not found in PATH. "
                f"Install {label} before running the devcoord control plane."
            )


class MemoryCoordStore:
    def __init__(self) -> None:
        self._records: dict[str, CoordRecord] = {}
        self._counter = 0

    def init_store(self) -> None:
        return None

    def list_records(
        self,
        milestone: str,
        *,
        kind: str | None = None,
    ) -> list[CoordRecord]:
        return [
            r
            for r in self._records.values()
            if r.has_label(COORD_LABEL)
            and r.metadata_str("milestone") == milestone
            and (kind is None or r.metadata_str(KIND_KEY) == kind)
        ]

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
        self._counter += 1
        record_id = f"coord-{self._counter}"
        record = CoordRecord(
            record_id=record_id,
            title=title,
            description=description,
            record_type=record_type,
            status=status,
            labels=tuple(sorted(set(labels))),
            metadata=dict(metadata),
            assignee=assignee,
            parent_id=parent_id,
        )
        self._records[record_id] = record
        return record

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
        old = self._records[record_id]
        updated = CoordRecord(
            record_id=old.record_id,
            title=old.title if title is None else title,
            description=old.description if description is None else description,
            record_type=old.record_type,
            status=old.status if status is None else status,
            labels=old.labels if labels is None else tuple(sorted(set(labels))),
            metadata=old.metadata if metadata is None else dict(metadata),
            assignee=old.assignee if assignee is None else assignee,
            parent_id=old.parent_id,
            created_at=old.created_at,
            updated_at=old.updated_at,
            closed_at=old.closed_at,
        )
        self._records[record_id] = updated
        return updated
