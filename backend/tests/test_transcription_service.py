import asyncio
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

from operation_manager import OperationManager  # noqa: E402
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


class ControllableTranscriber:
    def __init__(self, fail_after_partial=False):
        self.release = threading.Event()
        self.first_started = threading.Event()
        self.first_returned = threading.Event()
        self.second_started = threading.Event()
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

        while not self.release.wait(0.01):
            if control and control.stop_requested:
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
        return TranscriptionService(OperationManager(), transcriber, self.publish)

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
        self.assertEqual(stopped["status"], "stopped")
        await self.assert_event_set(transcriber.first_returned)
        await asyncio.sleep(0.05)

        snapshot = service.manager.get(operation["operation_id"])
        self.assertEqual(snapshot["partial_result"]["checkpoint"], 2.0)
        self.assertFalse(self.transcript_path.exists())

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

        saved = json.loads(self.transcript_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["full_text"], "готовый текст")
        self.assertNotIn("is_complete", saved)
        self.assertNotIn("checkpoint", saved)
        self.assertEqual(len(transcriber.calls), 1)
        self.assertEqual(list(Path(self.temp_dir.name).glob("*.tmp-*")), [])

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


if __name__ == "__main__":
    unittest.main()
