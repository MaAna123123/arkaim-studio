"""Thread-safe in-memory state for long-running local operations."""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone


ACTIVE_STATUSES = {"queued", "running", "paused"}
RESUMABLE_STATUSES = ACTIVE_STATUSES | {"stopped"}
TERMINAL_STATUSES = {"stopped", "completed", "failed", "cancelled"}


class OperationConflictError(RuntimeError):
    """Raised when an operation cannot make the requested state change."""


class OperationControl:
    """Cooperative pause/stop signals shared with a synchronous worker."""

    def __init__(self) -> None:
        self._can_run = threading.Event()
        self._can_run.set()
        self._stop = threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def pause(self) -> None:
        self._can_run.clear()

    def resume(self) -> None:
        self._can_run.set()

    def request_stop(self) -> None:
        self._stop.set()
        self._can_run.set()

    def wait_if_paused(self) -> bool:
        self._can_run.wait()
        return not self.stop_requested


class OperationManager:
    _TRANSITIONS = {
        "queued": {"running", "failed", "cancelled"},
        "running": {"paused", "stopped", "completed", "failed", "cancelled"},
        "paused": {"running", "stopped", "failed", "cancelled"},
        "stopped": {"running", "cancelled"},
        "completed": set(),
        "failed": set(),
        "cancelled": set(),
    }

    def __init__(self, max_terminal_records: int = 100) -> None:
        self._records: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._max_terminal_records = max(0, max_terminal_records)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, operation_type: str, video_id: str) -> dict:
        with self._lock:
            existing = self._find_record(operation_type, video_id, RESUMABLE_STATUSES)
            if existing is not None:
                return self._snapshot(existing)

            now = self._now()
            operation_id = str(uuid.uuid4())
            record = {
                "operation_id": operation_id,
                "operation_type": operation_type,
                "video_id": video_id,
                "status": "queued",
                "progress_pct": 0,
                "message": "",
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
                "eta_seconds": None,
                "error": None,
                "partial_result": None,
                "control": OperationControl(),
            }
            self._records[operation_id] = record
            return self._snapshot(record)

    def get(self, operation_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(operation_id)
            return self._snapshot(record) if record is not None else None

    def public_snapshot(self, operation_id: str) -> dict:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None:
                raise KeyError(operation_id)
            return self._snapshot(record)

    def find_for_video(
        self,
        video_id: str,
        include_stopped: bool = True,
        operation_type: str = "transcription",
    ) -> dict | None:
        statuses = RESUMABLE_STATUSES if include_stopped else ACTIVE_STATUSES
        with self._lock:
            record = self._find_record(operation_type, video_id, statuses)
            return self._snapshot(record) if record is not None else None

    def list_operations(
        self,
        video_id: str | None = None,
        active_only: bool = False,
    ) -> list[dict]:
        with self._lock:
            records = [
                record
                for record in self._records.values()
                if (video_id is None or record["video_id"] == video_id)
                and (not active_only or record["status"] in RESUMABLE_STATUSES)
            ]
            records.sort(key=lambda item: item["created_at"], reverse=True)
            return [self._snapshot(record) for record in records]

    def transition(self, operation_id: str, target: str) -> dict:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None:
                raise KeyError(operation_id)

            current = record["status"]
            if target not in self._TRANSITIONS.get(current, set()):
                raise OperationConflictError(
                    f"Cannot transition operation from {current} to {target}"
                )

            if target == "paused":
                record["control"].pause()
            elif target == "stopped":
                record["control"].request_stop()
            elif target == "running":
                if current == "stopped":
                    record["control"] = OperationControl()
                else:
                    record["control"].resume()

            now = self._now()
            record["status"] = target
            record["updated_at"] = now
            if target == "running" and record["started_at"] is None:
                record["started_at"] = now
            if target == "running":
                record["finished_at"] = None
                record["error"] = None
            elif target in TERMINAL_STATUSES:
                record["finished_at"] = now

            if target in TERMINAL_STATUSES:
                self._evict_old_terminal_records()
            return self._snapshot(record)

    def update(self, operation_id: str, **fields) -> dict:
        forbidden = {"operation_id", "operation_type", "video_id", "status", "control"}
        if forbidden.intersection(fields):
            raise ValueError("Operation identity and status must use dedicated methods")
        with self._lock:
            record = self._records.get(operation_id)
            if record is None:
                raise KeyError(operation_id)
            for name, value in fields.items():
                if name not in record:
                    raise ValueError(f"Unknown operation field: {name}")
                record[name] = copy.deepcopy(value)
            record["updated_at"] = self._now()
            return self._snapshot(record)

    def control(self, operation_id: str) -> OperationControl:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None:
                raise KeyError(operation_id)
            return record["control"]

    def _find_record(
        self,
        operation_type: str,
        video_id: str,
        statuses: set[str],
    ) -> dict | None:
        matches = (
            record
            for record in self._records.values()
            if record["operation_type"] == operation_type
            and record["video_id"] == video_id
            and record["status"] in statuses
        )
        return max(matches, key=lambda item: item["created_at"], default=None)

    def _evict_old_terminal_records(self) -> None:
        terminal = sorted(
            (
                record
                for record in self._records.values()
                if record["status"] in TERMINAL_STATUSES
            ),
            key=lambda item: item["updated_at"],
        )
        excess = len(terminal) - self._max_terminal_records
        for record in terminal[: max(0, excess)]:
            self._records.pop(record["operation_id"], None)

    @staticmethod
    def _snapshot(record: dict) -> dict:
        return {
            name: copy.deepcopy(value)
            for name, value in record.items()
            if name != "control"
        }
