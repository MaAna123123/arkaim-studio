"""Background orchestration for resumable local Whisper transcription."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable

try:
    from .operation_manager import OperationConflictError, OperationManager
except ImportError:
    from operation_manager import OperationConflictError, OperationManager


logger = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(
        self,
        manager: OperationManager,
        transcriber,
        publisher: Callable[[dict], Awaitable[None]],
    ) -> None:
        self.manager = manager
        self.transcriber = transcriber
        self._publisher = publisher
        self._metadata: dict[str, dict] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._lock = threading.RLock()

    def start(
        self,
        video_id: str,
        video_path: str,
        language: str | None,
        transcript_path: Path,
        loop: asyncio.AbstractEventLoop,
    ) -> dict:
        operation = self.manager.create("transcription", video_id)
        operation_id = operation["operation_id"]
        with self._lock:
            self._metadata.setdefault(
                operation_id,
                {
                    "video_path": video_path,
                    "language": language,
                    "transcript_path": Path(transcript_path),
                    "loop": loop,
                },
            )
            already_scheduled = operation_id in self._futures

        if operation["status"] == "queued" and not already_scheduled:
            operation = self.manager.update(
                operation_id,
                progress_pct=5,
                message="Запуск Whisper...",
            )
            self._schedule(operation_id, resume=False, loop=loop)
            self._publish_snapshot(operation_id, loop)
        return operation

    def pause(self, operation_id: str) -> dict:
        snapshot = self.manager.transition(operation_id, "paused")
        self._publish_for_operation(operation_id)
        return snapshot

    def resume(
        self,
        operation_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> dict:
        current = self.manager.get(operation_id)
        if current is None:
            raise KeyError(operation_id)
        if current["status"] == "paused":
            snapshot = self.manager.transition(operation_id, "running")
            self._publish_snapshot(operation_id, loop)
            return snapshot
        if current["status"] != "stopped":
            raise OperationConflictError(
                f"Cannot resume operation from {current['status']}"
            )

        snapshot = self.manager.transition(operation_id, "running")
        with self._lock:
            if operation_id not in self._metadata:
                raise OperationConflictError("Operation has no resumable worker metadata")
            self._metadata[operation_id]["loop"] = loop
        self._schedule(operation_id, resume=True, loop=loop)
        self._publish_snapshot(operation_id, loop)
        return snapshot

    def stop(self, operation_id: str) -> dict:
        snapshot = self.manager.transition(operation_id, "stopped")
        self._publish_for_operation(operation_id)
        return snapshot

    def _schedule(
        self,
        operation_id: str,
        resume: bool,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        with self._lock:
            previous = self._futures.get(operation_id)
            if previous is not None and not previous.done():
                previous.add_done_callback(
                    lambda _future: loop.call_soon_threadsafe(
                        self._launch, operation_id, resume, loop
                    )
                )
                return
        self._launch(operation_id, resume, loop)

    def _launch(
        self,
        operation_id: str,
        resume: bool,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        future = loop.run_in_executor(
            None,
            self._run_worker,
            operation_id,
            resume,
            loop,
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
    ) -> None:
        with self._lock:
            metadata = dict(self._metadata[operation_id])

        current = self.manager.get(operation_id)
        if current is None or current["status"] == "stopped":
            return
        if current["status"] == "queued":
            self.manager.transition(operation_id, "running")

        control = self.manager.control(operation_id)
        retained = self.manager.get(operation_id).get("partial_result") if resume else None
        checkpoint = float((retained or {}).get("checkpoint", 0.0))
        started_at = time.monotonic()

        def on_progress(message: str, progress_pct: int) -> None:
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
            self.manager.update(
                operation_id,
                partial_result=copy.deepcopy(partial),
            )
            self._publish_snapshot(operation_id, loop)

        try:
            result = self.transcriber.transcribe(
                metadata["video_path"],
                metadata["language"],
                on_progress,
                control=control,
                on_partial=on_partial,
                resume_from=checkpoint,
                retained_result=retained,
            )
            self.manager.update(operation_id, partial_result=copy.deepcopy(result))

            latest = self.manager.get(operation_id)
            if not result.get("is_complete") or latest["status"] == "stopped":
                self._publish_snapshot(operation_id, loop)
                return

            final_result = copy.deepcopy(result)
            final_result.pop("is_complete", None)
            final_result.pop("checkpoint", None)
            self._write_final_transcript(
                metadata["transcript_path"], operation_id, final_result
            )
            self.manager.update(
                operation_id,
                partial_result=final_result,
                progress_pct=100,
                message="Транскрипция завершена!",
                eta_seconds=0,
            )
            self.manager.transition(operation_id, "completed")
            self._publish_snapshot(operation_id, loop)
        except Exception as exc:
            logger.exception("Transcription worker failed")
            safe_error = f"Ошибка транскрипции: {str(exc)[:500]}"
            self.manager.update(
                operation_id,
                error=safe_error,
                message="Транскрипция завершилась с ошибкой",
                eta_seconds=None,
            )
            latest = self.manager.get(operation_id)
            if latest and latest["status"] in {"queued", "running", "paused"}:
                self.manager.transition(operation_id, "failed")
            self._publish_snapshot(operation_id, loop)

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
