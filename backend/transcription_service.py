"""Background orchestration for resumable local Whisper transcription."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

try:
    from .operation_manager import OperationConflictError, OperationManager
    from .project_store import ProjectStore
except ImportError:
    from operation_manager import OperationConflictError, OperationManager
    from project_store import ProjectStore


logger = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(
        self,
        manager: OperationManager,
        transcriber,
        publisher: Callable[[dict], Awaitable[None]],
        project_store: ProjectStore | None = None,
    ) -> None:
        self.manager = manager
        self.transcriber = transcriber
        self._publisher = publisher
        self.project_store = project_store
        self._metadata: dict[str, dict] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._video_operations: dict[tuple[str, str], str] = {}
        self._deleting_videos: set[str] = set()
        self._action_locks: dict[str, threading.Lock] = {}
        self._video_action_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()

    def start(
        self,
        video_id: str,
        video_path: str,
        language: str | None,
        transcript_path: Path,
        loop: asyncio.AbstractEventLoop,
    ) -> dict:
        with self._lock:
            if video_id in self._deleting_videos:
                raise OperationConflictError("Project is being deleted")
            key = ("transcription", video_id)
            known_id = self._video_operations.get(key)
            if known_id:
                known = self.manager.get(known_id)
                reusable = known and known["status"] in {
                    "queued", "running", "paused", "stopped"
                }
                completed_with_file = (
                    known
                    and known["status"] == "completed"
                    and Path(transcript_path).exists()
                )
                if reusable or completed_with_file:
                    return known

            operation = self.manager.create("transcription", video_id)
            operation_id = operation["operation_id"]
            self._video_operations[key] = operation_id
            self._metadata.setdefault(
                operation_id,
                {
                    "video_path": video_path,
                    "language": language,
                    "transcript_path": Path(transcript_path),
                    "loop": loop,
                    "generation": 1,
                    "checkpoint_lease": self._new_checkpoint_lease(),
                },
            )
            already_scheduled = operation_id in self._futures

        if operation["status"] == "queued" and not already_scheduled:
            operation = self.manager.update(
                operation_id,
                progress_pct=5,
                message="Запуск Whisper...",
                language=language,
            )
            self._schedule(operation_id, resume=False, loop=loop)
            self._publish_snapshot(operation_id, loop)
        return operation

    def restart_from_zero(
        self,
        video_id: str,
        video_path: str,
        language: str | None,
        transcript_path: Path,
        loop: asyncio.AbstractEventLoop,
        previous_operation_id: str | None = None,
    ) -> dict:
        """Atomically replace a stopped recovery snapshot with a fresh worker.

        The worker is scheduled behind a gate. The old checkpoint remains the
        durable authority until the new operation's initial checkpoint is
        written. A scheduling or persistence failure restores the stopped
        in-memory operation that the existing checkpoint describes.
        """
        if self.project_store is None:
            raise ValueError("Project storage is required for transcription restart")

        with self._video_action_lock(video_id):
            with self._lock:
                if video_id in self._deleting_videos:
                    raise OperationConflictError("Project is being deleted")
            if self.project_store.load_transcript(video_id) is not None:
                raise OperationConflictError("Completed transcript already exists")
            return self._restart_from_zero_locked(
                video_id,
                video_path,
                language,
                transcript_path,
                loop,
                previous_operation_id,
            )

    def begin_video_deletion(self, video_id: str) -> None:
        """Prevent new workers from starting while project files are removed."""
        with self._video_action_lock(video_id):
            with self._lock:
                if video_id in self._deleting_videos:
                    raise OperationConflictError("Project is already being deleted")
                self._deleting_videos.add(video_id)

    def abort_video_deletion(self, video_id: str) -> None:
        """Release a deletion claim when no project file was removed."""
        with self._video_action_lock(video_id):
            with self._lock:
                self._deleting_videos.discard(video_id)

    def _restart_from_zero_locked(
        self,
        video_id: str,
        video_path: str,
        language: str | None,
        transcript_path: Path,
        loop: asyncio.AbstractEventLoop,
        previous_operation_id: str | None = None,
    ) -> dict:
        """Commit a replacement while holding the project-wide action lock."""

        previous = None
        previous_metadata = None
        key = ("transcription", video_id)
        with self._lock:
            if previous_operation_id is not None:
                previous, operation = self.manager.begin_replacement(
                    previous_operation_id
                )
                previous_metadata = self._metadata.get(previous_operation_id)
                previous_lease = (previous_metadata or {}).get("checkpoint_lease")
                if previous_lease is not None:
                    previous_lease.clear()
                self._metadata.pop(previous_operation_id, None)
            else:
                operation = self.manager.create("transcription", video_id)
                if operation["status"] != "queued":
                    raise OperationConflictError("Current transcription is still active")

            operation_id = operation["operation_id"]
            launch_gate = threading.Event()
            checkpoint_lease = self._new_checkpoint_lease()
            self._video_operations[key] = operation_id
            self._metadata[operation_id] = {
                "video_path": video_path,
                "language": language,
                "transcript_path": Path(transcript_path),
                "loop": loop,
                "generation": 1,
                "checkpoint_lease": checkpoint_lease,
                "launch_gate": launch_gate,
                "checkpoint_committed": False,
            }

        try:
            operation = self.manager.update(
                operation_id,
                progress_pct=5,
                message="Запуск Whisper...",
                language=language,
            )
            self._schedule(operation_id, resume=False, loop=loop)
            checkpoint = self.manager.get(operation_id)
            if checkpoint is None:
                raise RuntimeError("Replacement operation disappeared before commit")
            if self.project_store.save_checkpoint(
                video_id,
                checkpoint,
                is_current=checkpoint_lease.is_set,
            ) is None:
                raise RuntimeError("Replacement checkpoint ownership was superseded")
        except Exception:
            self._rollback_uncommitted_restart(
                operation_id,
                previous,
                previous_metadata,
                key,
            )
            raise

        with self._lock:
            metadata = self._metadata.get(operation_id)
            if metadata is None:
                raise RuntimeError("Replacement operation disappeared after commit")
            metadata["checkpoint_committed"] = True
            metadata["launch_gate"].set()
        self._publish_snapshot(operation_id, loop)
        return operation

    def _rollback_uncommitted_restart(
        self,
        operation_id: str,
        previous: dict | None,
        previous_metadata: dict | None,
        key: tuple[str, str],
    ) -> None:
        with self._lock:
            metadata = self._metadata.pop(operation_id, None)
            if metadata is not None:
                metadata.get("launch_gate", threading.Event()).set()
            if previous is not None:
                restored = self.manager.rollback_replacement(previous, operation_id)
                self._video_operations[key] = restored["operation_id"]
                if previous_metadata is not None:
                    previous_lease = previous_metadata.get("checkpoint_lease")
                    if previous_lease is not None:
                        previous_lease.set()
                    self._metadata[restored["operation_id"]] = previous_metadata
            else:
                self.manager.discard(operation_id)
                self._video_operations.pop(key, None)

    def recover_projects(self) -> list[dict]:
        """Rebuild resumable operations without scheduling any workers.

        A valid final transcript is authoritative. Recovery deliberately leaves
        any stale checkpoint beside it untouched; startup must never risk the
        completed result merely to clean up an obsolete recovery artifact.
        """
        if self.project_store is None:
            return []

        recovered = []
        listing = self.project_store.list_projects()
        project_ids = [
            summary.get("project_id")
            for summary in listing.get("projects", [])
            if summary.get("project_id")
        ]
        projects_root = Path(self.project_store.projects_root)
        if projects_root.is_dir():
            project_ids.extend(
                project_directory.name
                for project_directory in projects_root.iterdir()
                if project_directory.is_dir()
                and project_directory.name not in project_ids
            )

        for project_id in project_ids:
            if not project_id:
                continue
            try:
                checkpoint = self.project_store.load_checkpoint(project_id)
                if checkpoint is None:
                    continue
                project = self._load_recovery_project_record(project_id)
                project_id = self._validated_recovery_identity(
                    project_id, project, checkpoint
                )
                try:
                    final_transcript = self.project_store.load_transcript(project_id)
                except (OSError, ValueError, json.JSONDecodeError):
                    logger.exception(
                        "Invalid final transcript for project %s; recovering checkpoint",
                        project_id,
                    )
                    final_transcript = None
                if final_transcript is not None:
                    continue
                source_path = Path(project["source_path"])
                if not source_path.is_absolute():
                    source_path = Path(self.project_store.project_root) / source_path
                legacy_root = getattr(
                    self.project_store, "legacy_transcripts_dir", None
                )
                transcript_path = (
                    Path(legacy_root) / f"{project_id}.json"
                    if legacy_root is not None
                    else Path(self.project_store.projects_root)
                    / project_id
                    / "transcript.json"
                )
                metadata = {
                    "video_path": str(source_path),
                    "language": checkpoint.get("language"),
                    "transcript_path": transcript_path,
                    "loop": None,
                    "generation": 1,
                    "checkpoint_lease": self._new_checkpoint_lease(),
                }
                restored = self.manager.restore(checkpoint)
                if restored is None:
                    continue
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                logger.exception("Could not recover transcription project %s", project_id)
                continue

            with self._lock:
                self._metadata[restored["operation_id"]] = metadata
                self._video_operations[("transcription", project_id)] = restored[
                    "operation_id"
                ]
            recovered.append(restored)
        return recovered

    def _load_recovery_project_record(self, project_id: str) -> dict:
        """Load validated project metadata without opening the final transcript."""
        store = self.project_store
        project_file = Path(store.projects_root) / project_id / "project.json"
        read_json = getattr(store, "_read_json", None)
        validate_schema = getattr(store, "_validate_project_schema", None)
        project_lock = getattr(store, "_project_lock", None)
        normalize_source = getattr(store, "_relative_source_path", None)
        if not all(callable(method) for method in (read_json, validate_schema)):
            return store.get_project(project_id)

        if callable(project_lock):
            with project_lock(project_id):
                project = read_json(project_file)
                validate_schema(project, project_id)
        else:
            project = read_json(project_file)
            validate_schema(project, project_id)
        if callable(normalize_source):
            project["source_path"] = normalize_source(project.get("source_path"))
        return project

    @staticmethod
    def _validated_recovery_identity(
        scanned_project_id: str,
        project: dict,
        checkpoint: dict,
    ) -> str:
        identities = (
            scanned_project_id,
            project.get("project_id"),
            project.get("video_id"),
            checkpoint.get("video_id"),
        )
        try:
            normalized = [str(uuid.UUID(str(identity))) for identity in identities]
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Invalid recovery project identity") from exc
        if len(set(normalized)) != 1:
            raise ValueError("Recovery project identities do not match")
        return normalized[0]

    def pause(self, operation_id: str) -> dict:
        with self._operation_action_lock(operation_id):
            with self._lock:
                current = self.manager.get(operation_id)
                if current is None:
                    raise KeyError(operation_id)
                if current["status"] != "running" or current.get("pending_action"):
                    raise OperationConflictError(
                        f"Cannot pause operation from {current['status']}"
                    )
                self.manager.control(operation_id).pause()
                snapshot = self.manager.update(
                    operation_id,
                    pending_action="pause",
                    message="Останавливаю Whisper на безопасной границе...",
                )
        self._publish_for_operation(operation_id)
        return snapshot

    def resume(
        self,
        operation_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> dict:
        current = self.manager.get(operation_id)
        if (
            self.project_store is not None
            and current is not None
            and current["status"] == "stopped"
            and not self.project_store.source_status(current["video_id"])["exists"]
        ):
            raise OperationConflictError("Source file is missing")

        with self._operation_action_lock(operation_id):
            with self._lock:
                current = self.manager.get(operation_id)
                if current is None:
                    raise KeyError(operation_id)
                if current["status"] == "paused":
                    if current.get("pending_action"):
                        raise OperationConflictError(
                            "Operation already has a pending action"
                        )
                    self.manager.control(operation_id).resume()
                    snapshot = self.manager.update(
                        operation_id,
                        pending_action="resume",
                        message="Возобновляю Whisper...",
                    )
                    self._publish_snapshot(operation_id, loop)
                    return snapshot
                if current["status"] != "stopped":
                    raise OperationConflictError(
                        f"Cannot resume operation from {current['status']}"
                    )

                if operation_id not in self._metadata:
                    raise OperationConflictError(
                        "Operation has no resumable worker metadata"
                    )
                self._metadata[operation_id]["loop"] = loop
                self._metadata[operation_id]["generation"] += 1
                previous_lease = self._metadata[operation_id].get("checkpoint_lease")
                if previous_lease is not None:
                    previous_lease.clear()
                self._metadata[operation_id]["checkpoint_lease"] = (
                    self._new_checkpoint_lease()
                )
                generation = self._metadata[operation_id]["generation"]
                snapshot = self.manager.transition(operation_id, "running")
                snapshot = self.manager.update(operation_id, pending_action=None)
            self._schedule(operation_id, resume=True, loop=loop, generation=generation)
            self._publish_snapshot(operation_id, loop)
            return snapshot

    def stop(self, operation_id: str) -> dict:
        with self._operation_action_lock(operation_id):
            with self._lock:
                current = self.manager.get(operation_id)
                if current is None:
                    raise KeyError(operation_id)
                if current["status"] not in {"running", "paused"}:
                    raise OperationConflictError(
                        f"Cannot stop operation from {current['status']}"
                    )
                self.manager.control(operation_id).request_stop()
                snapshot = self.manager.update(
                    operation_id,
                    pending_action="stop",
                    message="Останавливаю Whisper и сохраняю прогресс...",
                )
        self._publish_for_operation(operation_id)
        return snapshot

    def _schedule(
        self,
        operation_id: str,
        resume: bool,
        loop: asyncio.AbstractEventLoop,
        generation: int | None = None,
    ) -> None:
        with self._lock:
            if generation is None:
                generation = self._metadata[operation_id]["generation"]
            previous = self._futures.get(operation_id)
            if previous is not None and not previous.done():
                previous.add_done_callback(
                    lambda _future: loop.call_soon_threadsafe(
                        self._launch, operation_id, resume, loop, generation
                    )
                )
                return
        self._launch(operation_id, resume, loop, generation)

    def _launch(
        self,
        operation_id: str,
        resume: bool,
        loop: asyncio.AbstractEventLoop,
        generation: int,
    ) -> None:
        with self._lock:
            metadata = self._metadata.get(operation_id)
            current = self.manager.get(operation_id)
            if (
                metadata is None
                or metadata["generation"] != generation
                or current is None
                or current["status"] not in {"queued", "running"}
            ):
                return
        future = loop.run_in_executor(
            None,
            self._run_worker,
            operation_id,
            resume,
            loop,
            generation,
        )
        with self._lock:
            self._futures[operation_id] = future

        def forget(completed_future):
            with self._lock:
                if self._futures.get(operation_id) is completed_future:
                    self._futures.pop(operation_id, None)

        future.add_done_callback(forget)

    def _run_worker(
        self,
        operation_id: str,
        resume: bool,
        loop: asyncio.AbstractEventLoop,
        generation: int,
    ) -> None:
        with self._lock:
            metadata_record = self._metadata.get(operation_id)
            if (
                metadata_record is None
                or metadata_record["generation"] != generation
            ):
                return
            launch_gate = metadata_record.get("launch_gate")

        if launch_gate is not None:
            launch_gate.wait()
            with self._lock:
                metadata_record = self._metadata.get(operation_id)
                if (
                    metadata_record is None
                    or metadata_record["generation"] != generation
                    or not metadata_record.get("checkpoint_committed")
                ):
                    return

        with self._lock:
            metadata = dict(self._metadata[operation_id])

        current = self.manager.get(operation_id)
        if current is None or current["status"] not in {"queued", "running"}:
            return
        if current["status"] == "queued":
            self.manager.transition(operation_id, "running")

        control = self.manager.control(operation_id)
        retained = self.manager.get(operation_id).get("partial_result") if resume else None
        checkpoint = float((retained or {}).get("checkpoint", 0.0))
        started_at = time.monotonic()

        def on_progress(message: str, progress_pct: int) -> None:
            if not self._is_current_generation(operation_id, generation):
                return
            pct = max(0, min(int(progress_pct), 100))
            elapsed = max(0.0, time.monotonic() - started_at)
            eta = None
            if 0 < pct < 100:
                eta = max(0, round(elapsed * (100 - pct) / pct))
            self.manager.update(
                operation_id,
                progress_pct=pct,
                message=message,
                eta_seconds=eta,
            )
            self._publish_snapshot(operation_id, loop)

        def on_partial(partial: dict) -> None:
            if not self._is_current_generation(operation_id, generation):
                return
            self.manager.update(
                operation_id,
                partial_result=copy.deepcopy(partial),
            )
            self._persist_checkpoint(operation_id, generation)
            self._publish_snapshot(operation_id, loop)

        def on_control_state(state: str) -> None:
            with self._lock:
                if not self._is_current_generation(operation_id, generation):
                    return
                latest = self.manager.get(operation_id)
                if latest is None:
                    return
                if state == "paused" and latest["status"] == "running":
                    self.manager.transition(operation_id, "paused")
                elif state == "running" and latest["status"] == "paused":
                    self.manager.transition(operation_id, "running")
                elif state == "stopped" and latest["status"] in {"running", "paused"}:
                    self.manager.transition(operation_id, "stopped")
                else:
                    return
                self.manager.update(operation_id, pending_action=None)
            self._persist_checkpoint(operation_id, generation)
            self._publish_snapshot(operation_id, loop)

        try:
            result = self.transcriber.transcribe(
                metadata["video_path"],
                metadata["language"],
                on_progress,
                control=control,
                on_partial=on_partial,
                on_control_state=on_control_state,
                resume_from=checkpoint,
                retained_result=retained,
            )
            with self._operation_action_lock(operation_id):
                with self._lock:
                    if not self._is_current_generation(operation_id, generation):
                        return
                    self.manager.update(
                        operation_id, partial_result=copy.deepcopy(result)
                    )
                    latest = self.manager.get(operation_id)
                if not result.get("is_complete") and latest["status"] == "stopped":
                    self._persist_checkpoint(operation_id, generation)
            if result.get("is_complete") and latest.get("pending_action") == "stop":
                on_control_state("stopped")
                self._publish_snapshot(operation_id, loop)
                return
            if result.get("is_complete") and latest.get("pending_action") == "pause":
                # The final segment won the race with the pause boundary. Finishing
                # is safer than leaving a paused operation without a live worker.
                self.manager.update(operation_id, pending_action=None)
                latest = self.manager.get(operation_id)
            if not result.get("is_complete") or latest["status"] != "running":
                self._publish_snapshot(operation_id, loop)
                return

            final_result = copy.deepcopy(result)
            final_result.pop("is_complete", None)
            final_result.pop("checkpoint", None)
            video_id = latest["video_id"]
            with self._video_action_lock(video_id):
                with self._operation_action_lock(operation_id):
                    with self._lock:
                        latest = self.manager.get(operation_id)
                        if (
                            not self._is_current_generation(operation_id, generation)
                            or latest is None
                            or latest["status"] != "running"
                            or latest.get("pending_action")
                        ):
                            return

                    if self.project_store is not None:
                        self.project_store.save_transcript(
                            latest["video_id"], final_result
                        )

                    with self._lock:
                        latest = self.manager.get(operation_id)
                        if (
                            not self._is_current_generation(operation_id, generation)
                            or latest is None
                            or latest["status"] != "running"
                            or latest.get("pending_action")
                        ):
                            return
                        self._write_final_transcript(
                            metadata["transcript_path"], operation_id, final_result
                        )
                        self.manager.update(
                            operation_id,
                            partial_result=final_result,
                            progress_pct=100,
                            message="Транскрипция завершена!",
                            eta_seconds=0,
                            pending_action=None,
                        )
                        self.manager.transition(operation_id, "completed")

                    if self.project_store is not None:
                        try:
                            self.project_store.remove_checkpoint(latest["video_id"])
                        except OSError:
                            logger.exception(
                                "Final transcript saved but checkpoint cleanup failed"
                            )
            self._publish_snapshot(operation_id, loop)
        except Exception as exc:
            logger.exception("Transcription worker failed")
            safe_error = f"Ошибка транскрипции: {str(exc)[:500]}"
            if self._is_current_generation(operation_id, generation):
                self.manager.update(
                    operation_id,
                    error=safe_error,
                    message="Транскрипция завершилась с ошибкой",
                    eta_seconds=None,
                    pending_action=None,
                )
                latest = self.manager.get(operation_id)
                if latest and latest["status"] in {"queued", "running", "paused"}:
                    self.manager.transition(operation_id, "failed")
                self._persist_checkpoint(operation_id, generation)
                self._publish_snapshot(operation_id, loop)

    def _persist_checkpoint(self, operation_id: str, generation: int) -> None:
        if self.project_store is None:
            return
        snapshot = self.manager.get(operation_id)
        with self._lock:
            metadata = self._metadata.get(operation_id)
            if (
                snapshot is None
                or metadata is None
                or metadata["generation"] != generation
            ):
                return
            checkpoint_lease = metadata["checkpoint_lease"]
        self.project_store.save_checkpoint(
            snapshot["video_id"], snapshot, is_current=checkpoint_lease.is_set
        )

    @staticmethod
    def _new_checkpoint_lease() -> threading.Event:
        lease = threading.Event()
        lease.set()
        return lease

    def _operation_action_lock(self, operation_id: str) -> threading.Lock:
        with self._lock:
            return self._action_locks.setdefault(operation_id, threading.Lock())

    def _video_action_lock(self, video_id: str) -> threading.Lock:
        with self._lock:
            return self._video_action_locks.setdefault(video_id, threading.Lock())

    def _is_current_generation(self, operation_id: str, generation: int) -> bool:
        with self._lock:
            metadata = self._metadata.get(operation_id)
            return metadata is not None and metadata["generation"] == generation

    async def wait_for_terminal(self, operation_id: str) -> dict:
        while True:
            snapshot = self.manager.get(operation_id)
            if snapshot is None:
                raise KeyError(operation_id)
            if snapshot["status"] in {"completed", "failed", "stopped", "cancelled"}:
                return snapshot
            await asyncio.sleep(0.05)

    async def wait_for_idle(self, operation_id: str) -> None:
        """Wait until the worker has stopped touching project-owned files."""
        with self._lock:
            future = self._futures.get(operation_id)
        if future is not None and not future.done():
            await asyncio.shield(future)

    def forget(self, operation_id: str) -> None:
        """Drop all in-memory state after a worker is fully idle."""
        with self._operation_action_lock(operation_id):
            with self._lock:
                future = self._futures.get(operation_id)
                if future is not None and not future.done():
                    raise OperationConflictError("Operation worker is still active")
                metadata = self._metadata.pop(operation_id, None)
                if metadata is not None:
                    checkpoint_lease = metadata.get("checkpoint_lease")
                    if checkpoint_lease is not None:
                        checkpoint_lease.clear()
                self._futures.pop(operation_id, None)
                for key, known_id in list(self._video_operations.items()):
                    if known_id == operation_id:
                        self._video_operations.pop(key, None)
                self.manager.discard(operation_id)
        with self._lock:
            self._action_locks.pop(operation_id, None)

    async def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            for operation in self.manager.list_operations(active_only=True):
                operation_id = operation["operation_id"]
                if operation["status"] in {"running", "paused"}:
                    self.manager.control(operation_id).request_stop()
                    self.manager.update(operation_id, pending_action="stop")
                elif operation["status"] == "queued":
                    self.manager.transition(operation_id, "cancelled")
            futures = [future for future in self._futures.values() if not future.done()]
        if futures:
            await asyncio.wait(futures, timeout=timeout)

    @staticmethod
    def _write_final_transcript(
        transcript_path: Path,
        operation_id: str,
        data: dict,
    ) -> None:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = transcript_path.with_name(
            f"{transcript_path.name}.tmp-{operation_id}"
        )
        try:
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(transcript_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _publish_for_operation(self, operation_id: str) -> None:
        with self._lock:
            metadata = self._metadata.get(operation_id)
            loop = metadata.get("loop") if metadata else None
        if loop is not None:
            self._publish_snapshot(operation_id, loop)

    def _publish_snapshot(
        self,
        operation_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        snapshot = self.manager.get(operation_id)
        if snapshot is None:
            return
        event = {"type": "operation", **snapshot}
        coroutine = self._publisher(event)
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
