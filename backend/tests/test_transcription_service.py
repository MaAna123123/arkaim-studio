import asyncio
import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from operation_manager import OperationConflictError, OperationManager  # noqa: E402
from transcription_service import TranscriptionService  # noqa: E402


def partial_result(text="первая часть", checkpoint=2.0, complete=False):
    return {
        "full_text": text,
        "language": "ru",
        "language_probability": 0.98,
        "duration": 4.0,
        "segments": [
            {"start": 0.0, "end": checkpoint, "text": text, "words": []}
        ],
        "words": [],
        "paragraphs": [],
        "checkpoint": checkpoint,
        "is_complete": complete,
    }


class FakeProjectStore:
    def __init__(self):
        self.checkpoints = {}
        self.transcripts = {}
        self.calls = []
        self.fail_transcript_write = False
        self.status_provider = None
        self.status_at_remove = []

    def save_checkpoint(self, project_id, checkpoint):
        saved = copy.deepcopy(checkpoint)
        self.checkpoints[project_id] = saved
        self.calls.append(("save_checkpoint", project_id, saved))
        return copy.deepcopy(saved)

    def load_checkpoint(self, project_id):
        checkpoint = self.checkpoints.get(project_id)
        return copy.deepcopy(checkpoint) if checkpoint is not None else None

    def remove_checkpoint(self, project_id):
        if self.status_provider is not None:
            self.status_at_remove.append(self.status_provider())
        self.calls.append(("remove_checkpoint", project_id))
        return self.checkpoints.pop(project_id, None) is not None

    def save_transcript(self, project_id, transcript):
        saved = copy.deepcopy(transcript)
        if self.fail_transcript_write:
            self.calls.append(("save_transcript", project_id, saved))
            raise OSError("project transcript write failed")
        self.transcripts[project_id] = saved
        self.calls.append(("save_transcript", project_id, saved))
        return copy.deepcopy(saved)

    def load_transcript(self, project_id):
        transcript = self.transcripts.get(project_id)
        return copy.deepcopy(transcript) if transcript is not None else None

    def source_status(self, _project_id):
        return {"exists": True, "diagnostic": None}


class ControllableTranscriber:
    def __init__(self, fail_after_partial=False):
        self.release = threading.Event()
        self.first_started = threading.Event()
        self.first_returned = threading.Event()
        self.second_started = threading.Event()
        self.paused = threading.Event()
        self.fail_after_partial = fail_after_partial
        self.calls = []
        self._lock = threading.Lock()

    def transcribe(
        self,
        _video_path,
        language=None,
        on_progress=None,
        control=None,
        on_partial=None,
        on_control_state=None,
        resume_from=0.0,
        retained_result=None,
    ):
        with self._lock:
            call_number = len(self.calls) + 1
            self.calls.append(
                {
                    "language": language,
                    "resume_from": resume_from,
                    "retained_result": retained_result,
                }
            )
        (self.first_started if call_number == 1 else self.second_started).set()
        current = partial_result()
        if on_progress:
            on_progress("Распознаю речь", 50)
        if on_partial:
            on_partial(current)
        if self.fail_after_partial:
            raise RuntimeError("boom from fake worker")

        pause_reported = False
        while not self.release.wait(0.01):
            if control and control.pause_requested:
                if not pause_reported and on_control_state:
                    on_control_state("paused")
                    pause_reported = True
                    self.paused.set()
                if not control.wait_if_paused():
                    if on_control_state:
                        on_control_state("stopped")
                    self.first_returned.set()
                    return current
                if pause_reported and on_control_state:
                    on_control_state("running")
                    pause_reported = False
            if control and control.stop_requested:
                if on_control_state:
                    on_control_state("stopped")
                self.first_returned.set()
                return current

        if call_number > 1 and retained_result:
            result = partial_result("первая часть продолжение", 4.0, complete=True)
            result["segments"] = retained_result["segments"] + [
                {"start": 2.0, "end": 4.0, "text": "продолжение", "words": []}
            ]
            return result
        return partial_result("готовый текст", 4.0, complete=True)


class TranscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (PROJECT_ROOT / "temp").mkdir(exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "temp")
        self.transcript_path = Path(self.temp_dir.name) / "video-1.json"
        self.events = []
        self.store = FakeProjectStore()

        async def publish(event):
            self.events.append(event)

        self.publish = publish

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def wait_for_status(self, service, operation_id, expected, timeout=1.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            snapshot = service.manager.get(operation_id)
            if snapshot and snapshot["status"] == expected:
                return snapshot
            await asyncio.sleep(0.01)
        self.fail(f"Operation did not reach {expected}: {service.manager.get(operation_id)}")

    async def assert_event_set(self, event, timeout=1.0):
        self.assertTrue(await asyncio.to_thread(event.wait, timeout))

    def make_service(self, transcriber):
        service = TranscriptionService(
            OperationManager(), transcriber, self.publish, self.store
        )
        self.store.status_provider = lambda: service.manager.list_operations(
            video_id="video-1"
        )[0]["status"]
        return service

    async def wait_for_persisted_status(self, expected, timeout=1.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            checkpoint = self.store.checkpoints.get("video-1")
            if checkpoint and checkpoint["status"] == expected:
                return checkpoint
            await asyncio.sleep(0.01)
        self.fail(
            f"Checkpoint did not reach {expected}: "
            f"{self.store.checkpoints.get('video-1')}"
        )

    async def test_partial_and_paused_snapshots_are_persisted(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        operation = service.start(
            "video-1",
            "video.mp4",
            None,
            self.transcript_path,
            asyncio.get_running_loop(),
        )

        await self.assert_event_set(transcriber.first_started)
        partial = await self.wait_for_persisted_status("running")
        self.assertEqual(partial["partial_result"], partial_result())

        service.pause(operation["operation_id"])
        await self.assert_event_set(transcriber.paused)
        paused = await self.wait_for_persisted_status("paused")
        self.assertIsNone(paused["pending_action"])

        await service.shutdown(timeout=0.5)

    async def test_start_returns_before_worker_finishes(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)

        started_at = time.monotonic()
        operation = service.start(
            "video-1",
            "video.mp4",
            None,
            self.transcript_path,
            asyncio.get_running_loop(),
        )

        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertIn(operation["status"], {"queued", "running"})
        await self.assert_event_set(transcriber.first_started)
        transcriber.release.set()
        await self.wait_for_status(service, operation["operation_id"], "completed")

    async def test_duplicate_start_reuses_operation(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        loop = asyncio.get_running_loop()

        first = service.start(
            "video-1", "video.mp4", None, self.transcript_path, loop
        )
        second = service.start(
            "video-1", "video.mp4", None, self.transcript_path, loop
        )

        self.assertEqual(second["operation_id"], first["operation_id"])
        await self.assert_event_set(transcriber.first_started)
        self.assertEqual(len(transcriber.calls), 1)
        transcriber.release.set()
        await self.wait_for_status(service, first["operation_id"], "completed")

    async def test_stop_retains_checkpoint_without_writing_final_file(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        operation = service.start(
            "video-1",
            "video.mp4",
            None,
            self.transcript_path,
            asyncio.get_running_loop(),
        )
        await self.assert_event_set(transcriber.first_started)

        stopped = service.stop(operation["operation_id"])
        self.assertEqual(stopped["status"], "running")
        self.assertEqual(stopped["pending_action"], "stop")
        await self.assert_event_set(transcriber.first_returned)
        await self.wait_for_status(service, operation["operation_id"], "stopped")

        snapshot = service.manager.get(operation["operation_id"])
        self.assertEqual(snapshot["partial_result"]["checkpoint"], 2.0)
        self.assertFalse(self.transcript_path.exists())
        persisted = await self.wait_for_persisted_status("stopped")
        self.assertEqual(persisted["partial_result"], snapshot["partial_result"])

    async def test_resume_stopped_operation_reuses_partial_result(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        loop = asyncio.get_running_loop()
        operation = service.start(
            "video-1", "video.mp4", None, self.transcript_path, loop
        )
        await self.assert_event_set(transcriber.first_started)
        service.stop(operation["operation_id"])
        await self.assert_event_set(transcriber.first_returned)
        await self.wait_for_status(service, operation["operation_id"], "stopped")

        resumed = service.resume(operation["operation_id"], loop)
        self.assertEqual(resumed["status"], "running")
        await self.assert_event_set(transcriber.second_started)
        transcriber.release.set()
        await self.wait_for_status(service, operation["operation_id"], "completed")

        self.assertEqual(transcriber.calls[1]["resume_from"], 2.0)
        self.assertEqual(
            transcriber.calls[1]["retained_result"]["full_text"], "первая часть"
        )

    async def test_completion_atomically_writes_final_transcript_once(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        loop = asyncio.get_running_loop()

        first = service.start(
            "video-1", "video.mp4", None, self.transcript_path, loop
        )
        service.start("video-1", "video.mp4", None, self.transcript_path, loop)
        transcriber.release.set()
        await self.wait_for_status(service, first["operation_id"], "completed")

        call_names = [call[0] for call in self.store.calls]
        self.assertLess(
            call_names.index("save_transcript"),
            call_names.index("remove_checkpoint"),
        )
        self.assertEqual(self.store.status_at_remove, ["completed"])
        self.assertNotIn("video-1", self.store.checkpoints)
        self.assertEqual(
            self.store.transcripts["video-1"]["full_text"], "готовый текст"
        )

        saved = json.loads(self.transcript_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["full_text"], "готовый текст")
        self.assertNotIn("is_complete", saved)
        self.assertNotIn("checkpoint", saved)
        self.assertEqual(len(transcriber.calls), 1)
        self.assertEqual(list(Path(self.temp_dir.name).glob("*.tmp-*")), [])

        reused = service.start(
            "video-1", "video.mp4", None, self.transcript_path, loop
        )
        self.assertEqual(reused["operation_id"], first["operation_id"])
        self.assertEqual(reused["status"], "completed")
        self.assertEqual(len(transcriber.calls), 1)

    async def test_final_project_write_failure_fails_and_keeps_checkpoint(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        operation = service.start(
            "video-1",
            "video.mp4",
            None,
            self.transcript_path,
            asyncio.get_running_loop(),
        )
        self.store.fail_transcript_write = True
        transcriber.release.set()

        failed = await self.wait_for_status(
            service, operation["operation_id"], "failed"
        )

        self.assertIn("project transcript write failed", failed["error"])
        self.assertIn("video-1", self.store.checkpoints)
        self.assertEqual(self.store.checkpoints["video-1"]["status"], "failed")
        self.assertNotIn("remove_checkpoint", [call[0] for call in self.store.calls])
        self.assertFalse(self.transcript_path.exists())

    async def test_pause_and_resume_wait_for_worker_acknowledgement(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        loop = asyncio.get_running_loop()
        operation = service.start(
            "video-1", "video.mp4", None, self.transcript_path, loop
        )
        await self.assert_event_set(transcriber.first_started)

        requested = service.pause(operation["operation_id"])
        self.assertEqual(requested["status"], "running")
        self.assertEqual(requested["pending_action"], "pause")
        await self.assert_event_set(transcriber.paused)
        await self.wait_for_status(service, operation["operation_id"], "paused")

        requested = service.resume(operation["operation_id"], loop)
        self.assertEqual(requested["status"], "paused")
        self.assertEqual(requested["pending_action"], "resume")
        await self.wait_for_status(service, operation["operation_id"], "running")
        transcriber.release.set()
        await self.wait_for_status(service, operation["operation_id"], "completed")

    async def test_shutdown_unblocks_paused_worker(self):
        transcriber = ControllableTranscriber()
        service = self.make_service(transcriber)
        operation = service.start(
            "video-1", "video.mp4", None, self.transcript_path,
            asyncio.get_running_loop(),
        )
        await self.assert_event_set(transcriber.first_started)
        service.pause(operation["operation_id"])
        await self.assert_event_set(transcriber.paused)

        await service.shutdown(timeout=0.5)

        await self.assert_event_set(transcriber.first_returned)
        self.assertEqual(
            service.manager.get(operation["operation_id"])["status"], "stopped"
        )

    async def test_final_write_is_serialized_against_stop(self):
        entered = threading.Event()
        release_write = threading.Event()

        class BlockingWriteService(TranscriptionService):
            def _write_final_transcript(inner_self, path, operation_id, data):
                entered.set()
                release_write.wait(1.0)
                return super(BlockingWriteService, inner_self)._write_final_transcript(
                    path, operation_id, data
                )

        transcriber = ControllableTranscriber()
        service = BlockingWriteService(OperationManager(), transcriber, self.publish)
        operation = service.start(
            "video-1", "video.mp4", None, self.transcript_path,
            asyncio.get_running_loop(),
        )
        await self.assert_event_set(transcriber.first_started)
        transcriber.release.set()
        await self.assert_event_set(entered)

        stop_task = asyncio.create_task(
            asyncio.to_thread(service.stop, operation["operation_id"])
        )
        await asyncio.sleep(0.02)
        self.assertFalse(stop_task.done())
        release_write.set()
        with self.assertRaises(OperationConflictError):
            await stop_task
        completed = await self.wait_for_status(
            service, operation["operation_id"], "completed"
        )
        self.assertEqual(completed["pending_action"], None)
        self.assertTrue(self.transcript_path.exists())

    async def test_worker_failure_retains_partial_and_sets_safe_error(self):
        transcriber = ControllableTranscriber(fail_after_partial=True)
        service = self.make_service(transcriber)
        operation = service.start(
            "video-1",
            "video.mp4",
            None,
            self.transcript_path,
            asyncio.get_running_loop(),
        )

        failed = await self.wait_for_status(
            service, operation["operation_id"], "failed"
        )

        self.assertEqual(failed["partial_result"]["checkpoint"], 2.0)
        self.assertIn("boom from fake worker", failed["error"])
        self.assertFalse(self.transcript_path.exists())
        persisted = await self.wait_for_persisted_status("failed")
        self.assertEqual(persisted["error"], failed["error"])


if __name__ == "__main__":
    unittest.main()
