import json
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from operation_manager import OperationConflictError, OperationManager  # noqa: E402


class OperationManagerTests(unittest.TestCase):
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
