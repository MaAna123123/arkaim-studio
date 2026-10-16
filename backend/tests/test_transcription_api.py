import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
from operation_manager import OperationManager  # noqa: E402


class FakeTranscriptionService:
    def __init__(self, manager):
        self.manager = manager

    def start(self, video_id, _video_path, _language, _transcript_path, _loop):
        return self.manager.create("transcription", video_id)

    def pause(self, operation_id):
        return self.manager.transition(operation_id, "paused")

    def resume(self, operation_id, _loop):
        return self.manager.transition(operation_id, "running")

    def stop(self, operation_id):
        return self.manager.transition(operation_id, "stopped")

    async def wait_for_terminal(self, operation_id):
        return self.manager.get(operation_id)

    async def shutdown(self, timeout=2.0):
        return None


class TranscriptionApiTests(unittest.TestCase):
    def setUp(self):
        self.manager = OperationManager()
        self.service = FakeTranscriptionService(self.manager)
        self.temp_dir = tempfile.TemporaryDirectory(dir=main.TEMP_DIR)
        self.transcripts = Path(self.temp_dir.name)
        self.patchers = [
            patch.object(main, "operation_manager", self.manager),
            patch.object(main, "transcription_service", self.service),
            patch.object(main, "TRANSCRIPTS_DIR", self.transcripts),
            patch.object(main, "_find_video", return_value="video.mp4"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_new_start_returns_202_and_duplicate_returns_existing_200(self):
        first = self.client.post(
            "/api/video/transcribe", json={"video_id": "video-1"}
        )
        second = self.client.post(
            "/api/video/transcribe", json={"video_id": "video-1"}
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            second.json()["operation_id"], first.json()["operation_id"]
        )

    def test_existing_final_transcript_keeps_compatible_response(self):
        transcript = {"full_text": "готово", "segments": [], "words": []}
        (self.transcripts / "video-1.json").write_text(
            json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
        )

        response = self.client.post(
            "/api/video/transcribe", json={"video_id": "video-1"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), transcript)

    def test_unknown_operation_returns_404(self):
        response = self.client.get("/api/operations/missing-operation")

        self.assertEqual(response.status_code, 404)

    def test_invalid_transition_returns_409(self):
        operation = self.manager.create("transcription", "video-1")

        response = self.client.post(
            f"/api/operations/{operation['operation_id']}/pause"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.manager.get(operation["operation_id"])["status"], "queued"
        )

    def test_query_filters_by_video_and_includes_stopped_operation(self):
        ignored = self.manager.create("transcription", "video-2")
        self.manager.transition(ignored["operation_id"], "running")
        operation = self.manager.create("transcription", "video-1")
        self.manager.transition(operation["operation_id"], "running")
        self.manager.transition(operation["operation_id"], "stopped")

        response = self.client.get(
            "/api/operations", params={"video_id": "video-1", "active": "true"}
        )

        self.assertEqual(response.status_code, 200)
        operations = response.json()["operations"]
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["operation_id"], operation["operation_id"])
        self.assertEqual(operations[0]["status"], "stopped")

    def test_pause_resume_and_stop_return_updated_snapshot(self):
        operation = self.manager.create("transcription", "video-1")
        self.manager.transition(operation["operation_id"], "running")
        operation_id = operation["operation_id"]

        paused = self.client.post(f"/api/operations/{operation_id}/pause")
        resumed = self.client.post(f"/api/operations/{operation_id}/resume")
        stopped = self.client.post(f"/api/operations/{operation_id}/stop")

        self.assertEqual(paused.json()["status"], "paused")
        self.assertEqual(resumed.json()["status"], "running")
        self.assertEqual(stopped.json()["status"], "stopped")

    def test_transcript_analysis_conflicts_with_resumable_operation(self):
        operation = self.manager.create("transcription", "video-1")
        self.manager.transition(operation["operation_id"], "running")

        response = self.client.post(
            "/api/video/analyze",
            json={"video_id": "video-1", "mode": "transcript", "prompt": ""},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Whisper", response.json()["detail"])

    def test_transcript_analysis_uses_managed_service_for_missing_transcript(self):
        service = self.service

        def completed_start(video_id, _video_path, _language, transcript_path, _loop):
            operation = service.manager.create("transcription", video_id)
            service.manager.transition(operation["operation_id"], "running")
            Path(transcript_path).write_text(
                json.dumps({"full_text": "готово", "segments": [], "words": []}),
                encoding="utf-8",
            )
            return service.manager.transition(operation["operation_id"], "completed")

        class FakeAnalyzer:
            async def analyze_from_transcript(self, _data, _prompt, _progress):
                return []

        with (
            patch.object(service, "start", side_effect=completed_start),
            patch.object(main, "_build_analyzer", return_value=FakeAnalyzer()),
            patch.object(
                main.transcriber,
                "transcribe",
                side_effect=AssertionError("analysis bypassed TranscriptionService"),
            ),
        ):
            response = self.client.post(
                "/api/video/analyze",
                json={"video_id": "video-1", "mode": "transcript", "prompt": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"segments": []})


class OperationWebSocketPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_operation_event_preserves_operation_id(self):
        class RecordingSocket:
            def __init__(self):
                self.messages = []

            async def send_json(self, message):
                self.messages.append(message)

        socket = RecordingSocket()
        previous = list(main.active_connections)
        main.active_connections[:] = [socket]
        try:
            await main.broadcast(
                {
                    "type": "operation",
                    "operation_id": "operation-123",
                    "video_id": "video-1",
                    "status": "running",
                }
            )
        finally:
            main.active_connections[:] = previous

        self.assertEqual(socket.messages[0]["operation_id"], "operation-123")


if __name__ == "__main__":
    unittest.main()
