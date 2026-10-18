import asyncio
import copy
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from operation_manager import OperationConflictError, OperationManager  # noqa: E402
from project_store import ProjectStore  # noqa: E402
from transcription_service import TranscriptionService  # noqa: E402


def partial_result(checkpoint=12.5):
    return {
        "full_text": "первая часть",
        "segments": [{"start": 0.0, "end": checkpoint, "text": "первая часть"}],
        "words": [],
        "paragraphs": [],
        "checkpoint": checkpoint,
        "is_complete": False,
    }


class StopAfterPartialTranscriber:
    def __init__(self):
        self.called = threading.Event()

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
        result = partial_result()
        on_progress("Сохранён первый фрагмент", 35)
        on_partial(copy.deepcopy(result))
        on_control_state("stopped")
        self.called.set()
        return result


class CompleteFromCheckpointTranscriber:
    def __init__(self):
        self.called = threading.Event()
        self.resume_from = None
        self.retained_result = None

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
        self.resume_from = resume_from
        self.retained_result = copy.deepcopy(retained_result)
        self.called.set()
        segments = copy.deepcopy((retained_result or {}).get("segments", []))
        segments.append({"start": resume_from, "end": 20.0, "text": "и финал"})
        return {
            "full_text": "первая часть и финал",
            "segments": segments,
            "words": [],
            "paragraphs": [],
            "checkpoint": 20.0,
            "is_complete": True,
        }


class BlockUntilShutdownTranscriber:
    def __init__(self):
        self.partial_saved = threading.Event()

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
        result = partial_result(8.0)
        on_partial(copy.deepcopy(result))
        self.partial_saved.set()
        while not control.stop_requested:
            time.sleep(0.01)
        on_control_state("stopped")
        return result


class ProjectRecoveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (PROJECT_ROOT / "temp").mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "temp")
        self.root = Path(self.temp_dir.name)
        self.project_id = str(uuid.uuid4())
        self.source = self.root / "backend" / "uploads" / "lesson.mp4"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"video")
        self.store = ProjectStore(self.root)
        self.store.create_project(
            video_id=self.project_id,
            title="Lesson",
            source_filename=self.source.name,
            source_path=self.source,
            video={"duration": 20.0},
        )
        self.transcript_path = (
            self.store.projects_root / self.project_id / "transcript.json"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    async def publish(_event):
        return None

    def service(self, store, manager, transcriber):
        return TranscriptionService(manager, transcriber, self.publish, store)

    async def create_saved_partial(self):
        manager = OperationManager()
        transcriber = StopAfterPartialTranscriber()
        service = self.service(self.store, manager, transcriber)
        operation = service.start(
            self.project_id,
            str(self.source),
            "ru",
            self.transcript_path,
            asyncio.get_running_loop(),
        )
        await asyncio.to_thread(transcriber.called.wait, 1.0)
        terminal = await service.wait_for_terminal(operation["operation_id"])
        self.assertEqual(terminal["status"], "stopped")
        self.assertEqual(
            self.store.load_checkpoint(self.project_id)["partial_result"]["checkpoint"],
            12.5,
        )
        return operation["operation_id"]

    async def test_recreate_recover_explicit_resume_and_finish(self):
        operation_id = await self.create_saved_partial()

        reopened_store = ProjectStore(self.root)
        reopened_manager = OperationManager()
        completing = CompleteFromCheckpointTranscriber()
        reopened_service = self.service(reopened_store, reopened_manager, completing)

        recovered = reopened_service.recover_projects()

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["operation_id"], operation_id)
        self.assertEqual(recovered[0]["status"], "stopped")
        self.assertFalse(completing.called.is_set(), "startup must never auto-resume")

        reopened_service.resume(operation_id, asyncio.get_running_loop())
        completed = await reopened_service.wait_for_terminal(operation_id)

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completing.resume_from, 12.5)
        self.assertEqual(completing.retained_result["full_text"], "первая часть")
        self.assertEqual(
            reopened_store.load_transcript(self.project_id)["full_text"],
            "первая часть и финал",
        )
        self.assertIsNone(reopened_store.load_checkpoint(self.project_id))

    async def test_shutdown_persists_partial_before_worker_finishes(self):
        manager = OperationManager()
        transcriber = BlockUntilShutdownTranscriber()
        service = self.service(self.store, manager, transcriber)
        operation = service.start(
            self.project_id,
            str(self.source),
            "ru",
            self.transcript_path,
            asyncio.get_running_loop(),
        )
        saved = await asyncio.to_thread(transcriber.partial_saved.wait, 1.0)
        self.assertTrue(saved)

        await service.shutdown(timeout=1.0)

        self.assertEqual(manager.get(operation["operation_id"])["status"], "stopped")
        checkpoint = ProjectStore(self.root).load_checkpoint(self.project_id)
        self.assertEqual(checkpoint["partial_result"]["checkpoint"], 8.0)
        recovered_manager = OperationManager()
        recovered_service = self.service(
            ProjectStore(self.root), recovered_manager, CompleteFromCheckpointTranscriber()
        )
        recovered = recovered_service.recover_projects()
        self.assertEqual(recovered[0]["status"], "stopped")

    async def test_missing_source_can_be_restored_then_resumed(self):
        operation_id = await self.create_saved_partial()
        original_checkpoint = self.store.load_checkpoint(self.project_id)
        self.source.unlink()

        reopened_store = ProjectStore(self.root)
        reopened_manager = OperationManager()
        completing = CompleteFromCheckpointTranscriber()
        reopened_service = self.service(reopened_store, reopened_manager, completing)
        recovered = reopened_service.recover_projects()
        self.assertEqual(recovered[0]["status"], "stopped")

        with self.assertRaisesRegex(OperationConflictError, "Source file is missing"):
            reopened_service.resume(operation_id, asyncio.get_running_loop())
        self.assertEqual(
            reopened_store.load_checkpoint(self.project_id), original_checkpoint
        )
        self.assertFalse(completing.called.is_set())

        self.source.write_bytes(b"restored video")
        reopened_service.resume(operation_id, asyncio.get_running_loop())
        completed = await reopened_service.wait_for_terminal(operation_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            reopened_store.load_transcript(self.project_id)["full_text"],
            "первая часть и финал",
        )
        self.assertIsNone(reopened_store.load_checkpoint(self.project_id))


if __name__ == "__main__":
    unittest.main()
