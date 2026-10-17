import asyncio
import copy
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from operation_manager import OperationConflictError, OperationManager  # noqa: E402
from transcription_service import TranscriptionService  # noqa: E402


def saved_partial(checkpoint=412.8):
    return {
        "full_text": "частичный текст",
        "segments": [{"start": 0.0, "end": checkpoint, "text": "часть"}],
        "words": [],
        "paragraphs": [],
        "checkpoint": checkpoint,
        "is_complete": False,
    }


def saved_checkpoint(project_id, operation_id=None, status="running"):
    return {
        "schema_version": 1,
        "operation_id": operation_id or str(uuid.uuid4()),
        "operation_type": "transcription",
        "video_id": project_id,
        "status": status,
        "progress_pct": 37,
        "message": "Распознавание продолжается",
        "created_at": "2026-10-17T08:00:00+00:00",
        "updated_at": "2026-10-17T08:10:00+00:00",
        "started_at": "2026-10-17T08:01:00+00:00",
        "finished_at": None,
        "eta_seconds": 120,
        "error": None,
        "pending_action": "pause",
        "partial_result": saved_partial(),
        "language": "ru",
    }


class RecoveryProjectStore:
    def __init__(self, root):
        self.project_root = Path(root)
        self.projects_root = self.project_root / "data" / "projects"
        self.legacy_transcripts_dir = self.project_root / "backend" / "transcripts"
        self.projects = {}
        self.checkpoints = {}
        self.transcripts = {}
        self.calls = []
        self.source_exists = {}

    def add_project(self, project_id, source_path="backend/uploads/lesson.mp4"):
        (self.projects_root / project_id).mkdir(parents=True, exist_ok=True)
        self.projects[project_id] = {
            "project_id": project_id,
            "video_id": project_id,
            "source_path": source_path,
        }
        self.source_exists.setdefault(project_id, True)

    def list_projects(self, limit=20):
        return {
            "projects": [
                {"project_id": project_id, "video_id": project_id}
                for project_id in list(self.projects)[:limit]
            ],
            "diagnostics": [],
        }

    def get_project(self, project_id):
        return copy.deepcopy(self.projects[project_id])

    def source_status(self, project_id):
        return {"exists": self.source_exists[project_id], "diagnostic": None}

    def save_checkpoint(self, project_id, checkpoint):
        saved = copy.deepcopy(checkpoint)
        self.checkpoints[project_id] = saved
        self.calls.append(("save_checkpoint", project_id, saved))
        return copy.deepcopy(saved)

    def load_checkpoint(self, project_id):
        self.calls.append(("load_checkpoint", project_id))
        checkpoint = self.checkpoints.get(project_id)
        return copy.deepcopy(checkpoint) if checkpoint is not None else None

    def remove_checkpoint(self, project_id):
        self.calls.append(("remove_checkpoint", project_id))
        return self.checkpoints.pop(project_id, None) is not None

    def save_transcript(self, project_id, transcript):
        saved = copy.deepcopy(transcript)
        self.transcripts[project_id] = saved
        self.calls.append(("save_transcript", project_id, saved))
        return copy.deepcopy(saved)

    def load_transcript(self, project_id):
        transcript = self.transcripts.get(project_id)
        return copy.deepcopy(transcript) if transcript is not None else None


class TrackingOperationManager(OperationManager):
    def __init__(self):
        super().__init__()
        self.restore_calls = []

    def restore(self, snapshot):
        self.restore_calls.append(copy.deepcopy(snapshot))
        return super().restore(snapshot)


class NeverStartTranscriber:
    def __init__(self):
        self.calls = []

    def transcribe(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("recovery must not start a worker")


class CapturingResumeTranscriber:
    def __init__(self):
        self.called = threading.Event()
        self.resume_from = None
        self.retained_result = None
        self.call_count = 0
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
            self.call_count += 1
            self.resume_from = resume_from
            self.retained_result = copy.deepcopy(retained_result)
        self.called.set()
        return copy.deepcopy(retained_result)


class TranscriptionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "temp")
        self.store = RecoveryProjectStore(self.temp_dir.name)
        self.project_id = "02afe1b0-c9e0-4746-861f-b06006bd6af0"
        self.operation_id = "a3e55f84-538c-4db7-bc61-5607953fa9a2"
        self.store.add_project(self.project_id)
        self.store.checkpoints[self.project_id] = saved_checkpoint(
            self.project_id, self.operation_id
        )
        self.manager = TrackingOperationManager()
        self.transcriber = NeverStartTranscriber()

        async def publish(_event):
            return None

        self.service = TranscriptionService(
            self.manager, self.transcriber, publish, self.store
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_recovery_restores_metadata_and_stopped_operation_without_launching_worker(self):
        original = copy.deepcopy(self.store.checkpoints[self.project_id])

        recovered = self.service.recover_projects()

        self.assertEqual(self.manager.restore_calls, [original])
        self.assertEqual(len(recovered), 1)
        snapshot = self.manager.get(self.operation_id)
        self.assertEqual(snapshot["status"], "stopped")
        self.assertEqual(snapshot["partial_result"], original["partial_result"])
        metadata = self.service._metadata[self.operation_id]
        self.assertEqual(
            metadata["video_path"],
            str(Path(self.temp_dir.name) / "backend" / "uploads" / "lesson.mp4"),
        )
        self.assertEqual(metadata["language"], "ru")
        self.assertIsNone(metadata["loop"])
        self.assertEqual(
            self.service._video_operations[("transcription", self.project_id)],
            self.operation_id,
        )
        self.assertEqual(self.transcriber.calls, [])
        self.assertEqual(self.service._futures, {})

    async def test_resume_passes_exact_persisted_checkpoint_and_retained_result(self):
        original_partial = copy.deepcopy(
            self.store.checkpoints[self.project_id]["partial_result"]
        )
        transcriber = CapturingResumeTranscriber()
        self.service.transcriber = transcriber
        self.service.recover_projects()

        self.service.resume(self.operation_id, asyncio.get_running_loop())

        called = await asyncio.to_thread(transcriber.called.wait, 1.0)
        self.assertTrue(called)
        self.assertEqual(transcriber.resume_from, 412.8)
        self.assertEqual(transcriber.retained_result, original_partial)

    async def test_concurrent_resume_requests_launch_only_one_worker(self):
        transcriber = CapturingResumeTranscriber()
        self.service.transcriber = transcriber
        self.service.recover_projects()
        loop = asyncio.get_running_loop()
        barrier = threading.Barrier(2)

        def resume_together():
            barrier.wait()
            return self.service.resume(self.operation_id, loop)

        results = await asyncio.gather(
            asyncio.to_thread(resume_together),
            asyncio.to_thread(resume_together),
            return_exceptions=True,
        )
        called = await asyncio.to_thread(transcriber.called.wait, 1.0)
        await asyncio.sleep(0.05)

        self.assertTrue(called)
        self.assertEqual(transcriber.call_count, 1)
        self.assertEqual(
            sum(isinstance(result, OperationConflictError) for result in results), 1
        )

    def test_valid_final_transcript_wins_over_stale_checkpoint_without_cleanup(self):
        stale_checkpoint = copy.deepcopy(self.store.checkpoints[self.project_id])
        self.store.transcripts[self.project_id] = {
            "full_text": "готовый текст",
            "segments": [],
        }

        recovered = self.service.recover_projects()

        self.assertEqual(recovered, [])
        self.assertIsNone(self.manager.get(self.operation_id))
        self.assertNotIn(self.operation_id, self.service._metadata)
        self.assertEqual(self.store.checkpoints[self.project_id], stale_checkpoint)
        self.assertNotIn("remove_checkpoint", [call[0] for call in self.store.calls])
        self.assertEqual(self.transcriber.calls, [])

    def test_resume_rejects_missing_source_without_changing_saved_data(self):
        original_project = copy.deepcopy(self.store.projects[self.project_id])
        original_checkpoint = copy.deepcopy(self.store.checkpoints[self.project_id])
        self.store.source_exists[self.project_id] = False
        self.service.recover_projects()
        loop = asyncio.new_event_loop()

        try:
            with self.assertRaises(OperationConflictError):
                self.service.resume(self.operation_id, loop)
        finally:
            loop.close()

        self.assertEqual(self.store.projects[self.project_id], original_project)
        self.assertEqual(
            self.store.checkpoints[self.project_id], original_checkpoint
        )
        self.assertEqual(self.manager.get(self.operation_id)["status"], "stopped")
        self.assertEqual(self.transcriber.calls, [])

    def test_invalid_project_metadata_does_not_leave_ghost_operation(self):
        self.store.projects[self.project_id].pop("source_path")

        recovered = self.service.recover_projects()

        self.assertEqual(recovered, [])
        self.assertIsNone(self.manager.get(self.operation_id))
        self.assertNotIn(self.operation_id, self.service._metadata)
        self.assertEqual(self.transcriber.calls, [])

    def test_recovery_is_not_limited_to_recent_project_listing(self):
        last_project_id = None
        last_operation_id = None
        for value in range(1000, 1020):
            project_id = str(uuid.UUID(int=value))
            operation_id = str(uuid.UUID(int=value + 1000))
            self.store.add_project(project_id)
            self.store.checkpoints[project_id] = saved_checkpoint(
                project_id, operation_id, status="stopped"
            )
            last_project_id = project_id
            last_operation_id = operation_id

        recovered = self.service.recover_projects()

        self.assertEqual(len(recovered), 21)
        self.assertEqual(self.manager.get(last_operation_id)["status"], "stopped")
        self.assertEqual(
            self.service._video_operations[("transcription", last_project_id)],
            last_operation_id,
        )


if __name__ == "__main__":
    unittest.main()
