from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from .model import CoordPaths
from .service_common import _none_if_placeholder as _none_if_placeholder
from .service_common import _utc_now
from .service_event import (
    heartbeat as _heartbeat,
)
from .service_event import (
    log_pending as _log_pending,
)
from .service_event import (
    ping as _ping,
)
from .service_event import (
    recovery_check as _recovery_check,
)
from .service_event import (
    stale_detected as _stale_detected,
)
from .service_event import (
    state_sync_ok as _state_sync_ok,
)
from .service_event import (
    unconfirmed_instruction as _unconfirmed_instruction,
)
from .service_gate import (
    ack as _ack,
)
from .service_gate import (
    gate_close as _gate_close,
)
from .service_gate import (
    gate_review as _gate_review,
)
from .service_gate import (
    init_control_plane as _init_control_plane,
)
from .service_gate import (
    open_gate as _open_gate,
)
from .service_gate import (
    phase_complete as _phase_complete,
)
from .service_projection import (
    audit as _audit,
)
from .service_projection import (
    close_milestone as _close_milestone,
)
from .service_projection import (
    render as _render,
)
from .store import CoordStore

__all__ = ["CoordService", "_none_if_placeholder", "_utc_now"]


@dataclass
class CoordService:
    paths: CoordPaths
    store: CoordStore
    now_fn: Callable[[], str] = field(default=lambda: _utc_now())

    def init_control_plane(self, milestone: str, *, run_date: str, roles: Sequence[str]) -> None:
        _init_control_plane(self, milestone, run_date=run_date, roles=roles)

    def open_gate(
        self,
        milestone: str,
        *,
        phase: str,
        gate_id: str,
        allowed_role: str,
        target_commit: str,
        task: str,
    ) -> None:
        _open_gate(
            self,
            milestone,
            phase=phase,
            gate_id=gate_id,
            allowed_role=allowed_role,
            target_commit=target_commit,
            task=task,
        )

    def ack(
        self,
        milestone: str,
        *,
        role: str,
        command: str,
        gate_id: str,
        commit: str,
        phase: str | None = None,
        task: str,
    ) -> None:
        _ack(
            self,
            milestone,
            role=role,
            command=command,
            gate_id=gate_id,
            commit=commit,
            phase=phase,
            task=task,
        )

    def heartbeat(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        status: str,
        task: str,
        eta_min: int | None,
        gate_id: str | None = None,
        target_commit: str | None = None,
        branch: str | None = None,
    ) -> None:
        _heartbeat(
            self,
            milestone,
            role=role,
            phase=phase,
            status=status,
            task=task,
            eta_min=eta_min,
            gate_id=gate_id,
            target_commit=target_commit,
            branch=branch,
        )

    def phase_complete(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        gate_id: str,
        commit: str,
        task: str,
        branch: str | None = None,
    ) -> None:
        _phase_complete(
            self,
            milestone,
            role=role,
            phase=phase,
            gate_id=gate_id,
            commit=commit,
            task=task,
            branch=branch,
        )

    def recovery_check(
        self,
        milestone: str,
        *,
        role: str,
        last_seen_gate: str,
        task: str,
    ) -> None:
        _recovery_check(self, milestone, role=role, last_seen_gate=last_seen_gate, task=task)

    def state_sync_ok(
        self,
        milestone: str,
        *,
        role: str,
        gate_id: str,
        target_commit: str,
        task: str,
    ) -> None:
        _state_sync_ok(
            self,
            milestone,
            role=role,
            gate_id=gate_id,
            target_commit=target_commit,
            task=task,
        )

    def stale_detected(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        task: str,
        gate_id: str | None = None,
        target_commit: str | None = None,
        ping_count: int | None = None,
    ) -> None:
        _stale_detected(
            self,
            milestone,
            role=role,
            phase=phase,
            task=task,
            gate_id=gate_id,
            target_commit=target_commit,
            ping_count=ping_count,
        )

    def ping(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        gate_id: str,
        task: str,
        target_commit: str | None = None,
        command_name: str = "PING",
    ) -> None:
        _ping(
            self,
            milestone,
            role=role,
            phase=phase,
            gate_id=gate_id,
            task=task,
            target_commit=target_commit,
            command_name=command_name,
        )

    def unconfirmed_instruction(
        self,
        milestone: str,
        *,
        role: str,
        command: str,
        phase: str,
        gate_id: str,
        task: str,
        target_commit: str | None = None,
        ping_count: int | None = None,
    ) -> None:
        _unconfirmed_instruction(
            self,
            milestone,
            role=role,
            command=command,
            phase=phase,
            gate_id=gate_id,
            task=task,
            target_commit=target_commit,
            ping_count=ping_count,
        )

    def log_pending(
        self,
        milestone: str,
        *,
        phase: str,
        task: str,
        gate_id: str | None = None,
        target_commit: str | None = None,
    ) -> None:
        _log_pending(
            self,
            milestone,
            phase=phase,
            task=task,
            gate_id=gate_id,
            target_commit=target_commit,
        )

    def audit(self, milestone: str) -> dict[str, Any]:
        return _audit(self, milestone)

    def gate_review(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        gate_id: str,
        result: str,
        report_commit: str,
        report_path: str,
        task: str,
    ) -> None:
        _gate_review(
            self,
            milestone,
            role=role,
            phase=phase,
            gate_id=gate_id,
            result=result,
            report_commit=report_commit,
            report_path=report_path,
            task=task,
        )

    def gate_close(
        self,
        milestone: str,
        *,
        phase: str,
        gate_id: str,
        result: str,
        report_commit: str,
        report_path: str,
        task: str,
    ) -> None:
        _gate_close(
            self,
            milestone,
            phase=phase,
            gate_id=gate_id,
            result=result,
            report_commit=report_commit,
            report_path=report_path,
            task=task,
        )

    def render(self, milestone: str) -> None:
        _render(self, milestone)

    def close_milestone(self, milestone: str) -> None:
        _close_milestone(self, milestone)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.paths.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.lock_file.touch(exist_ok=True)
        with self.paths.lock_file.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                with self.store.transaction():
                    yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
