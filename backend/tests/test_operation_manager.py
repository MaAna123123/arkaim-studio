import json
import sys
import unittest
import uuid
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from operation_manager import OperationConflictError, OperationManager  # noqa: E402


class OperationManagerTests(unittest.TestCase):
    def restored_snapshot(self, **overrides):
        snapshot = {
            "operation_id": "a3e55f84-538c-4db7-bc61-5607953fa9a2",
            "operation_type": "transcription",
            "video_id": "02afe1b0-c9e0-4746-861f-b06006bd6af0",
            "status": "running",
            "progress_pct": 37,
            "message": "Распознавание продолжается",
            "created_at": "2026-10-17T08:00:00+00:00",
            "updated_at": "2026-10-17T08:10:00+00:00",
            "started_at": "2026-10-17T08:01:00+00:00",
            "finished_at": None,
            "eta_seconds": 120,
            "error": None,
            "pending_action": "pause",
            "partial_result": {
                "checkpoint": 412.8,
                "full_text": "частичный текст",
                "segments": [],
                "words": [],
                "paragraphs": [],
                "duration": 1100,
                "is_complete": False,
            },
        }
        snapshot.update(overrides)
        return snapshot

    def test_restore_preserves_snapshot_data_with_fresh_control_and_no_work_started(self):
        manager = OperationManager()
        snapshot = self.restored_snapshot()

        restored = manager.restore(snapshot)

        self.assertEqual(restored["operation_id"], snapshot["operation_id"])
        self.assertEqual(restored["video_id"], snapshot["video_id"])
        self.assertEqual(restored["created_at"], snapshot["created_at"])
        self.assertEqual(restored["updated_at"], snapshot["updated_at"])
        self.assertEqual(restored["progress_pct"], 37)
        self.assertEqual(restored["partial_result"], snapshot["partial_result"])
        self.assertEqual(restored["status"], "stopped")
        self.assertFalse(manager.control(restored["operation_id"]).stop_requested)
        self.assertFalse(manager.control(restored["operation_id"]).pause_requested)
        self.assertEqual(manager.list_operations(active_only=True), [restored])

    def test_restore_rejects_duplicate_invalid_or_incomplete_identities(self):
        manager = OperationManager()
        snapshot = self.restored_snapshot(status="failed")
        manager.restore(snapshot)

        with self.assertRaises(ValueError):
            manager.restore(snapshot)
        with self.assertRaises(ValueError):
            manager.restore(self.restored_snapshot(operation_id="not-a-uuid"))
        with self.assertRaises(ValueError):
            manager.restore(self.restored_snapshot(video_id="not-a-uuid"))
        with self.assertRaises(ValueError):
            manager.restore(self.restored_snapshot(operation_type="export"))
        for identity_field in ("operation_id", "operation_type", "video_id"):
            incomplete = self.restored_snapshot()
            incomplete.pop(identity_field)
            with self.subTest(identity_field=identity_field):
                with self.assertRaises(ValueError):
                    manager.restore(incomplete)

    def test_restore_rejects_unknown_status_and_unsupported_fields(self):
        manager = OperationManager()

        with self.assertRaises(ValueError):
            manager.restore(self.restored_snapshot(status="unknown"))
        with self.assertRaises(ValueError):
            manager.restore(self.restored_snapshot(worker_id="not-supported"))

    def test_restore_normalizes_interrupted_statuses_to_stopped(self):
        recovery_message = "Прервано после закрытия приложения — можно продолжить"
        for status in ("queued", "running", "paused", "stopped"):
            with self.subTest(status=status):
                manager = OperationManager()
                restored = manager.restore(self.restored_snapshot(status=status))

                self.assertEqual(restored["status"], "stopped")
                self.assertEqual(restored["message"], recovery_message)
                self.assertIsNone(restored["pending_action"])
                self.assertIsNone(restored["eta_seconds"])

    def test_restore_keeps_failed_but_skips_completed_and_cancelled_snapshots(self):
        manager = OperationManager()

        failed = manager.restore(self.restored_snapshot(status="failed", error="модель недоступна"))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "модель недоступна")

        completed = self.restored_snapshot(
            operation_id=str(uuid.uuid4()), status="completed"
        )
        cancelled = self.restored_snapshot(
            operation_id=str(uuid.uuid4()), status="cancelled"
        )
        self.assertIsNone(manager.restore(completed))
        self.assertIsNone(manager.restore(cancelled))
        self.assertIsNone(manager.get(completed["operation_id"]))
        self.assertIsNone(manager.get(cancelled["operation_id"]))

    def test_valid_transcription_state_transitions(self):
        manager = OperationManager()
        operation = manager.create("transcription", "video-1")

        self.assertEqual(operation["status"], "queued")
        self.assertEqual(
            manager.transition(operation["operation_id"], "running")["status"],
            "running",
        )
        self.assertEqual(
            manager.transition(operation["operation_id"], "paused")["status"],
            "paused",
        )
        self.assertEqual(
            manager.transition(operation["operation_id"], "running")["status"],
            "running",
        )
        self.assertEqual(
            manager.transition(operation["operation_id"], "stopped")["status"],
            "stopped",
        )
        self.assertTrue(manager.control(operation["operation_id"]).stop_requested)

        resumed = manager.transition(operation["operation_id"], "running")
        self.assertEqual(resumed["status"], "running")
        self.assertFalse(manager.control(operation["operation_id"]).stop_requested)
        self.assertEqual(
            manager.transition(operation["operation_id"], "completed")["status"],
            "completed",
        )

    def test_invalid_transition_preserves_state(self):
        manager = OperationManager()
        operation = manager.create("transcription", "video-1")

        with self.assertRaises(OperationConflictError):
            manager.transition(operation["operation_id"], "completed")

        self.assertEqual(manager.get(operation["operation_id"])["status"], "queued")

    def test_duplicate_video_operation_returns_existing_record(self):
        manager = OperationManager()
        first = manager.create("transcription", "video-1")
        second = manager.create("transcription", "video-1")

        self.assertEqual(second["operation_id"], first["operation_id"])

        manager.transition(first["operation_id"], "cancelled")
        replacement = manager.create("transcription", "video-1")
        self.assertNotEqual(replacement["operation_id"], first["operation_id"])

    def test_terminal_retention_never_evicts_active_records(self):
        manager = OperationManager(max_terminal_records=100)
        active = manager.create("transcription", "active-video")
        manager.transition(active["operation_id"], "running")

        terminal_ids = []
        for index in range(105):
            operation = manager.create("export", f"finished-{index}")
            manager.transition(operation["operation_id"], "cancelled")
            terminal_ids.append(operation["operation_id"])

        self.assertIsNotNone(manager.get(active["operation_id"]))
        self.assertIsNone(manager.get(terminal_ids[0]))
        retained_terminal = [
            operation_id
            for operation_id in terminal_ids
            if manager.get(operation_id) is not None
        ]
        self.assertEqual(len(retained_terminal), 100)

    def test_public_snapshot_hides_threading_controls(self):
        manager = OperationManager()
        operation = manager.create("transcription", "video-1")
        manager.update(
            operation["operation_id"],
            progress_pct=42,
            partial_result={"text": "частичный текст", "segments": []},
        )

        snapshot = manager.public_snapshot(operation["operation_id"])

        self.assertNotIn("control", snapshot)
        self.assertEqual(snapshot["progress_pct"], 42)
        self.assertEqual(snapshot["partial_result"]["text"], "частичный текст")
        json.dumps(snapshot)


if __name__ == "__main__":
    unittest.main()
