import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
from operation_manager import OperationManager  # noqa: E402
from transcription_service import TranscriptionService  # noqa: E402
from project_store import ProjectStore  # noqa: E402


def project_checkpoint(project_id, *, status="running", **updates):
    checkpoint = {
        "schema_version": 1,
        "operation_id": str(uuid.uuid4()),
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
        "pending_action": None,
        "partial_result": {
            "checkpoint": 33.0,
            "full_text": "частичный текст",
            "segments": [],
            "words": [],
            "paragraphs": [],
            "is_complete": False,
        },
        "language": "ru",
    }
    checkpoint.update(updates)
    return checkpoint


class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=main.TEMP_DIR)
        self.project_root = Path(self.temp_dir.name)
        self.store = ProjectStore(
            self.project_root,
            legacy_transcripts_dir=self.project_root / "backend" / "transcripts",
        )
        self.project_id = str(uuid.uuid4())
        self.source = (
            self.project_root / "backend" / "uploads" / f"{self.project_id}.mp4"
        )
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"video")
        self.store.create_project(
            self.project_id,
            "Test lesson",
            "lesson.mp4",
            self.source,
            {"duration": 42.5, "width": 1920, "height": 1080},
        )
        self.manager = OperationManager()
        self.patchers = [
            patch.object(main, "project_store", self.store),
            patch.object(main, "operation_manager", self.manager),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_list_projects_returns_recent_public_summaries_without_paths(self):
        response = self.client.get("/api/projects", params={"limit": 20})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["projects"][0]["project_id"], self.project_id)
        self.assertTrue(response.json()["projects"][0]["source_exists"])
        self.assertNotIn("source_path", response.text)
        self.assertNotIn(str(self.project_root), response.text)

    def test_list_projects_rejects_out_of_range_limit(self):
        response = self.client.get("/api/projects", params={"limit": 0})

        self.assertEqual(response.status_code, 400)

    def test_get_project_returns_editor_and_derived_state_without_source_path(self):
        response = self.client.get(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["project_id"], self.project_id)
        self.assertEqual(body["editor"]["aspect_ratio"], "9:16")
        self.assertTrue(body["source_exists"])
        self.assertFalse(body["has_transcript"])
        self.assertIsNone(body["transcription"])
        self.assertNotIn("source_path", body)
        self.assertNotIn(str(self.project_root), response.text)

    def test_get_project_maps_invalid_and_missing_ids(self):
        invalid = self.client.get("/api/projects/not-a-uuid")
        missing = self.client.get(f"/api/projects/{uuid.uuid4()}")

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(missing.status_code, 404)

    def test_delete_project_removes_all_owned_artifacts_and_keeps_siblings(self):
        sibling_id = str(uuid.uuid4())
        directories = {
            "clips": self.project_root / "backend" / "clips",
            "thumbs": self.project_root / "backend" / "thumbs",
            "transcripts": self.project_root / "backend" / "transcripts",
            "temp": self.project_root / "backend" / "temp",
        }
        for directory in directories.values():
            directory.mkdir(parents=True, exist_ok=True)

        owned = [
            directories["clips"] / f"{self.project_id}_clip_1.mp4",
            directories["thumbs"] / f"{self.project_id}_thumb_001.jpg",
            directories["transcripts"] / f"{self.project_id}.json",
            directories["transcripts"] / f"{self.project_id}.chapters.json",
            directories["temp"] / f"{self.project_id}_work.tmp",
        ]
        sibling = [
            directories["clips"] / f"{sibling_id}_clip_1.mp4",
            directories["thumbs"] / f"{sibling_id}_thumb_001.jpg",
            directories["transcripts"] / f"{sibling_id}.json",
            directories["temp"] / f"{sibling_id}_work.tmp",
        ]
        for path in owned + sibling:
            path.write_bytes(b"data")

        with (
            patch.object(main, "CLIPS_DIR", directories["clips"]),
            patch.object(main, "THUMBS_DIR", directories["thumbs"]),
            patch.object(main, "TRANSCRIPTS_DIR", directories["transcripts"]),
            patch.object(main, "TEMP_DIR", directories["temp"]),
        ):
            response = self.client.delete(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "deleted": True,
            "project_id": self.project_id,
        })
        self.assertFalse(self.source.exists())
        self.assertFalse((self.store.projects_root / self.project_id).exists())
        self.assertTrue(all(not path.exists() for path in owned))
        self.assertTrue(all(path.exists() for path in sibling))

    def test_delete_project_unlinks_matching_symlink_without_deleting_sibling_artifact(self):
        clips = self.project_root / "backend" / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        sibling = clips / f"{uuid.uuid4()}_clip_1.mp4"
        sibling.write_bytes(b"sibling")
        owned_link = clips / f"{self.project_id}_clip_1.mp4"
        try:
            owned_link.symlink_to(sibling)
        except OSError as exc:
            self.skipTest(f"Symlinks are unavailable: {exc}")

        with patch.object(main, "CLIPS_DIR", clips):
            response = self.client.delete(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(owned_link.exists())
        self.assertTrue(sibling.exists())

    def test_delete_project_keeps_record_retryable_when_artifact_cleanup_fails(self):
        with patch.object(
            main,
            "_delete_project_artifacts",
            side_effect=OSError("cleanup failed"),
        ):
            response = self.client.delete(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 500)
        self.assertTrue(self.source.exists())
        self.assertTrue((self.store.projects_root / self.project_id / "project.json").is_file())

    def test_patch_project_updates_only_allowlisted_editor_state(self):
        response = self.client.patch(
            f"/api/projects/{self.project_id}",
            json={"selected": [1, 3], "text_ad_text": "Реклама"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["editor"]["selected"], [1, 3])
        self.assertEqual(response.json()["editor"]["text_ad_text"], "Реклама")
        self.assertNotIn("source_path", response.json())

    def test_patch_project_rejects_invalid_editor_value_without_mutating_data(self):
        project_file = self.store.projects_root / self.project_id / "project.json"
        before = project_file.read_bytes()

        response = self.client.patch(
            f"/api/projects/{self.project_id}",
            json={"export_padding": "not-a-number"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(project_file.read_bytes(), before)

    def test_patch_project_rejects_non_editor_fields(self):
        project_file = self.store.projects_root / self.project_id / "project.json"
        before = project_file.read_bytes()

        response = self.client.patch(
            f"/api/projects/{self.project_id}",
            json={
                "source_path": "backend/uploads/other.mp4",
                "operation_id": str(uuid.uuid4()),
                "transcript": {"full_text": "injected"},
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("редактор", response.json()["detail"].lower())
        self.assertEqual(project_file.read_bytes(), before)

    def test_detail_marks_parseable_malformed_checkpoint_non_resumable_without_leaking_it(self):
        checkpoint_file = self.store.projects_root / self.project_id / "checkpoint.json"
        self.store.save_checkpoint(self.project_id, {})
        before = checkpoint_file.read_bytes()

        response = self.client.get(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transcription"]["resume_available"], False)
        self.assertEqual(
            response.json()["transcription"]["diagnostic"]["code"],
            "checkpoint_invalid",
        )
        self.assertEqual(checkpoint_file.read_bytes(), before)

    def test_detail_public_checkpoint_projection_omits_paths_and_secrets(self):
        self.store.save_checkpoint(
            self.project_id,
            project_checkpoint(
                self.project_id,
                status="stopped",
                message=r"E:\\private\\source.mp4 api_key=hidden",
                error="oauth token=hidden subscription=https://example.test/private",
                partial_result={
                    "checkpoint": 33.0,
                    "source_path": r"E:\\private\\source.mp4",
                    "api_key": "hidden",
                    "nested": {"token": "hidden"},
                },
            ),
        )

        response = self.client.get(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 200)
        projection = response.json()["transcription"]
        self.assertFalse(projection["resume_available"])
        self.assertNotIn("message", projection)
        self.assertNotIn("error", projection)
        self.assertNotIn("partial_result", projection)
        self.assertNotIn(r"E:\\private", response.text)
        self.assertNotIn("api_key", response.text)
        self.assertNotIn("subscription=", response.text)

    def test_list_and_detail_suppress_stale_checkpoint_when_final_transcript_exists(self):
        self.store.save_checkpoint(self.project_id, project_checkpoint(self.project_id))
        self.store.save_transcript(
            self.project_id,
            {"full_text": "готовый текст", "segments": [], "words": []},
        )

        listing = self.client.get("/api/projects", params={"limit": 20})
        detail = self.client.get(f"/api/projects/{self.project_id}")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(listing.json()["projects"][0]["has_transcript"])
        self.assertIsNone(listing.json()["projects"][0]["transcription"])
        self.assertTrue(detail.json()["has_transcript"])
        self.assertIsNone(detail.json()["transcription"])

    def test_list_and_detail_prefer_recovered_stopped_operation_to_running_disk_checkpoint(self):
        checkpoint = project_checkpoint(self.project_id, status="running")
        self.store.save_checkpoint(self.project_id, checkpoint)
        restored = self.manager.restore(checkpoint)

        listing = self.client.get("/api/projects", params={"limit": 20})
        detail = self.client.get(f"/api/projects/{self.project_id}")

        self.assertEqual(restored["status"], "stopped")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            listing.json()["projects"][0]["transcription"]["operation_id"],
            checkpoint["operation_id"],
        )
        self.assertEqual(
            listing.json()["projects"][0]["transcription"]["status"], "stopped"
        )
        self.assertEqual(
            detail.json()["transcription"]["operation_id"],
            checkpoint["operation_id"],
        )
        self.assertEqual(detail.json()["transcription"]["status"], "stopped")

    def test_corrupt_project_returns_500_without_rewriting_saved_data(self):
        project_file = self.store.projects_root / self.project_id / "project.json"
        damaged = b'{"schema_version":'
        project_file.write_bytes(damaged)

        response = self.client.get(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(project_file.read_bytes(), damaged)


class IngestionProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=main.TEMP_DIR)
        self.project_root = Path(self.temp_dir.name)
        self.uploads = self.project_root / "backend" / "uploads"
        self.thumbs = self.project_root / "backend" / "thumbs"
        self.transcripts = self.project_root / "backend" / "transcripts"
        for directory in (self.uploads, self.thumbs, self.transcripts):
            directory.mkdir(parents=True)
        self.store = ProjectStore(
            self.project_root,
            legacy_transcripts_dir=self.transcripts,
        )
        self.patchers = [
            patch.object(main, "project_store", self.store),
            patch.object(main, "UPLOAD_DIR", self.uploads),
            patch.object(main, "THUMBS_DIR", self.thumbs),
            patch.object(main, "TRANSCRIPTS_DIR", self.transcripts),
            patch.object(
                main.processor,
                "extract_metadata",
                return_value={"duration": 61.5, "width": 1280, "height": 720},
            ),
            patch.object(main.processor, "extract_thumbnails", return_value=[]),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def _fake_download(self, _url, output_dir, _quality, video_id, _progress):
        destination = Path(output_dir) / f"{video_id}.mp4"
        destination.write_bytes(b"downloaded video")
        return destination

    def test_local_upload_creates_project_after_metadata_is_available(self):
        response = self.client.post(
            "/api/video/upload",
            files={"file": ("lesson.mp4", b"video bytes", "video/mp4")},
        )

        self.assertEqual(response.status_code, 200)
        project = self.store.get_project(response.json()["video_id"])
        self.assertEqual(project["source_filename"], "lesson.mp4")
        self.assertEqual(project["video"]["duration"], 61.5)
        self.assertTrue(self.store.source_status(project["project_id"])["exists"])

    def test_failed_local_metadata_does_not_create_project(self):
        with patch.object(
            main.processor,
            "extract_metadata",
            side_effect=ValueError("invalid media"),
        ):
            response = self.client.post(
                "/api/video/upload",
                files={"file": ("broken.mp4", b"not video", "video/mp4")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.list_projects()["projects"], [])

    def test_youtube_download_creates_project_with_downloaded_source(self):
        with (
            patch.object(main, "yt_get_info", return_value={"title": "Remote lesson"}),
            patch.object(
                main,
                "get_youtube_transcript",
                return_value={"has_transcript": False, "segments": [], "words": []},
            ),
            patch.object(main, "get_instant_clips", return_value=[]),
            patch.object(main, "yt_download", side_effect=self._fake_download),
        ):
            response = self.client.post(
                "/api/youtube/download",
                json={"url": "https://www.youtube.com/watch?v=example", "quality": "720"},
            )

        self.assertEqual(response.status_code, 200)
        project = self.store.get_project(response.json()["video_id"])
        self.assertEqual(project["title"], "Remote lesson")
        self.assertEqual(project["video"]["duration"], 61.5)
        self.assertTrue(self.store.source_status(project["project_id"])["exists"])

    def test_youtube_auto_creates_project_and_saves_editor_results(self):
        segment = {"start": 1.0, "end": 10.0, "label": "Moment"}
        clip = {"url": "clips/clip-1.mp4"}
        with (
            patch.object(main, "yt_get_info", return_value={"title": "Auto lesson"}),
            patch.object(
                main,
                "get_youtube_transcript",
                return_value={"has_transcript": False, "segments": [], "words": []},
            ),
            patch.object(main, "yt_download", side_effect=self._fake_download),
            patch.object(main, "_resolve_transcript", new=AsyncMock(return_value={
                "has_transcript": False, "segments": [], "words": [],
            })),
            patch.object(main, "get_instant_clips", return_value=[segment]),
            patch.object(main, "_export_segments", new=AsyncMock(return_value=[clip])),
        ):
            response = self.client.post(
                "/api/youtube/auto",
                json={"url": "https://www.youtube.com/watch?v=example", "quality": "720"},
            )

        self.assertEqual(response.status_code, 200)
        project = self.store.get_project(response.json()["video_id"])
        self.assertEqual(project["title"], "Auto lesson")
        self.assertEqual(project["editor"]["segments"], [segment])
        self.assertEqual(project["editor"]["clip_results"], [clip])

    def test_youtube_auto_without_subtitles_does_not_start_whisper(self):
        segment = {"start": 1.0, "end": 10.0, "label": "Moment"}
        clip = {"url": "clips/clip-1.mp4"}
        resolve_transcript = AsyncMock()
        with (
            patch.object(main, "yt_get_info", return_value={"title": "Auto lesson"}),
            patch.object(
                main,
                "get_youtube_transcript",
                return_value={"has_transcript": False, "segments": [], "words": []},
            ),
            patch.object(main, "yt_download", side_effect=self._fake_download),
            patch.object(main, "_resolve_transcript", new=resolve_transcript),
            patch.object(main, "get_instant_clips", return_value=[segment]),
            patch.object(main, "_export_segments", new=AsyncMock(return_value=[clip])) as export_segments,
        ):
            response = self.client.post(
                "/api/youtube/auto",
                json={
                    "url": "https://www.youtube.com/watch?v=example",
                    "quality": "720",
                    "burn_subtitles": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        resolve_transcript.assert_not_awaited()
        self.assertFalse(export_segments.await_args.kwargs["burn_subtitles"])


class RestartPartialTranscriber:
    """Lets the real worker start while retaining an incomplete replacement."""

    def __init__(self):
        self.started = threading.Event()

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
        self.started.set()
        return {
            "full_text": "restart partial",
            "segments": [],
            "words": [],
            "checkpoint": 0.0,
            "is_complete": False,
        }


class ProjectTranscriptionRestartTests(unittest.TestCase):
    def setUp(self):
        from operation_manager import OperationManager

        self.temp_dir = tempfile.TemporaryDirectory(dir=main.TEMP_DIR)
        self.project_root = Path(self.temp_dir.name)
        self.transcripts = self.project_root / "backend" / "transcripts"
        self.transcripts.mkdir(parents=True)
        self.store = ProjectStore(
            self.project_root,
            legacy_transcripts_dir=self.transcripts,
        )
        self.project_id = str(uuid.uuid4())
        self.source = (
            self.project_root / "backend" / "uploads" / f"{self.project_id}.mp4"
        )
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"video")
        self.store.create_project(
            self.project_id,
            "Restart lesson",
            self.source.name,
            self.source,
            {"duration": 90},
        )
        self.store.update_editor(
            self.project_id,
            {
                "segments": [{"start": 1, "end": 4, "label": "Saved"}],
                "clip_results": [{"label": "Exported clip"}],
            },
        )
        self.manager = OperationManager()
        old = self.manager.create("transcription", self.project_id)
        self.manager.transition(old["operation_id"], "running")
        self.manager.update(
            old["operation_id"],
            progress_pct=63,
            partial_result={"checkpoint": 57.0, "segments": [{"text": "partial"}]},
        )
        self.old_operation = self.manager.transition(old["operation_id"], "stopped")
        self.store.save_checkpoint(self.project_id, self.old_operation)
        self.transcriber = RestartPartialTranscriber()

        async def publish(_event):
            return None

        self.service = TranscriptionService(
            self.manager,
            self.transcriber,
            publish,
            self.store,
        )
        self.patchers = [
            patch.object(main, "project_store", self.store),
            patch.object(main, "operation_manager", self.manager),
            patch.object(main, "transcription_service", self.service),
            patch.object(main, "TRANSCRIPTS_DIR", self.transcripts),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_restart_replaces_recoverable_state_from_zero_and_keeps_editor_data(self):
        before_editor = self.store.get_project(self.project_id)["editor"]

        response = self.client.post(
            f"/api/projects/{self.project_id}/transcription/restart"
        )

        self.assertEqual(response.status_code, 202)
        replacement = response.json()
        self.assertNotEqual(
            replacement["operation_id"], self.old_operation["operation_id"]
        )
        self.assertEqual(replacement["progress_pct"], 5)
        self.assertIsNone(replacement["partial_result"])
        self.assertTrue(self.transcriber.started.wait(1.0))
        self.assertEqual(
            self.manager.get(self.old_operation["operation_id"])["status"],
            "cancelled",
        )
        self.assertEqual(
            self.store.load_checkpoint(self.project_id)["operation_id"],
            replacement["operation_id"],
        )
        self.assertEqual(
            self.store.get_project(self.project_id)["editor"], before_editor
        )

    def test_restart_with_missing_source_returns_409_without_mutating_saved_data(self):
        project_file = self.store.projects_root / self.project_id / "project.json"
        checkpoint_file = self.store.projects_root / self.project_id / "checkpoint.json"
        before_project = project_file.read_bytes()
        before_checkpoint = checkpoint_file.read_bytes()
        self.source.unlink()

        response = self.client.post(
            f"/api/projects/{self.project_id}/transcription/restart"
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("исход", response.json()["detail"].lower())
        self.assertEqual(project_file.read_bytes(), before_project)
        self.assertEqual(checkpoint_file.read_bytes(), before_checkpoint)
        self.assertEqual(
            self.manager.get(self.old_operation["operation_id"])["status"],
            "stopped",
        )
        self.assertFalse(self.transcriber.started.is_set())

    def test_restart_startup_failure_keeps_saved_checkpoint(self):
        checkpoint_file = self.store.projects_root / self.project_id / "checkpoint.json"
        before_checkpoint = checkpoint_file.read_bytes()

        with patch.object(
            self.service,
            "restart_from_zero",
            side_effect=OSError("Whisper worker is unavailable"),
            create=True,
        ):
            response = self.client.post(
                f"/api/projects/{self.project_id}/transcription/restart"
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(checkpoint_file.read_bytes(), before_checkpoint)


class StartupRecoveryApiTests(unittest.TestCase):
    """Startup must expose stopped recovery snapshots without launching Whisper."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=main.TEMP_DIR)
        self.project_root = Path(self.temp_dir.name)
        self.transcripts = self.project_root / "backend" / "transcripts"
        self.transcripts.mkdir(parents=True)
        self.store = ProjectStore(
            self.project_root,
            legacy_transcripts_dir=self.transcripts,
        )
        self.project_id = str(uuid.uuid4())
        self.operation_id = str(uuid.uuid4())
        self.source = (
            self.project_root / "backend" / "uploads" / f"{self.project_id}.mp4"
        )
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"video")
        self.store.create_project(
            self.project_id,
            "Recovered lesson",
            self.source.name,
            self.source,
            {"duration": 90},
        )
        self.store.save_checkpoint(
            self.project_id,
            {
                "schema_version": 1,
                "operation_id": self.operation_id,
                "operation_type": "transcription",
                "video_id": self.project_id,
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
                    "checkpoint": 33.0,
                    "full_text": "частичный текст",
                    "segments": [],
                    "words": [],
                    "paragraphs": [],
                    "duration": 90,
                    "is_complete": False,
                },
                "language": "ru",
            },
        )
        from operation_manager import OperationManager

        class NeverStartTranscriber:
            def __init__(self):
                self.calls = []

            def transcribe(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("startup recovery must not launch Whisper")

        self.manager = OperationManager()
        self.transcriber = NeverStartTranscriber()

        async def publish(_event):
            return None

        self.service = TranscriptionService(
            self.manager,
            self.transcriber,
            publish,
            self.store,
        )
        self.patchers = [
            patch.object(main, "project_store", self.store),
            patch.object(main, "operation_manager", self.manager),
            patch.object(main, "transcription_service", self.service),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_startup_recovers_operation_for_existing_endpoint_without_launching_worker(self):
        with TestClient(main.app) as client:
            response = client.get(f"/api/operations/{self.operation_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "stopped")
        self.assertEqual(response.json()["progress_pct"], 37)
        self.assertEqual(self.transcriber.calls, [])


class CompositionExportValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=main.TEMP_DIR)
        self.root = Path(self.temp_dir.name)
        self.banner_dir = self.root / "banners"
        self.ad_dir = self.root / "ads"
        self.banner_dir.mkdir()
        self.ad_dir.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def segment(start=0.0, end=60.0):
        return {
            "start": start,
            "end": end,
            "label": "Edited",
            "composition": {
                "source_start": start,
                "source_end": end,
                "output_aspect_ratio": "9:16",
                "main_layer": {
                    "crop": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "destination": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "fit": "cover",
                },
            },
        }

    def test_exact_source_boundaries_are_accepted(self):
        segment = self.segment(0.0, 60.0)

        validated = main.validate_composition_for_source(segment, 60.0)

        self.assertIs(validated, segment)

    def test_source_range_past_duration_is_rejected_in_russian(self):
        with self.assertRaises(main.HTTPException) as raised:
            main.validate_composition_for_source(self.segment(0.0, 60.1), 60.0)

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("исходного видео", raised.exception.detail)

    def test_source_end_within_metadata_tolerance_is_snapped_to_exact_duration(self):
        segment = self.segment(0.0, 60.0005)

        main.validate_composition_for_source(segment, 60.0)

        self.assertEqual(segment["end"], 60.0)
        self.assertEqual(segment["composition"]["source_end"], 60.0)

    def test_missing_enabled_banner_and_ad_are_rejected(self):
        for layer_name, layer in (
            ("banner_layer", {"enabled": True}),
            ("ad_layer", {"enabled": True, "mode": "overlay"}),
        ):
            segment = self.segment()
            segment["composition"][layer_name] = layer
            with (
                patch.object(main, "BANNER_DIR", self.banner_dir),
                patch.object(main, "ADS_DIR", self.ad_dir),
                self.assertRaises(main.HTTPException) as raised,
            ):
                main.validate_composition_for_source(segment, 60.0)
            self.assertEqual(raised.exception.status_code, 422)

    def test_legacy_segment_remains_valid_without_composition(self):
        segment = {"start": 1.0, "end": 2.0, "label": "Legacy"}

        self.assertIs(main.validate_composition_for_source(segment, 60.0), segment)

    def test_replaced_banner_revision_is_rejected(self):
        banner = self.banner_dir / "current_banner.mp4"
        banner.write_bytes(b"new banner")
        segment = self.segment()
        segment["composition"]["banner_layer"] = {
            "enabled": True,
            "asset_revision": "old-revision",
        }

        with (
            patch.object(main, "BANNER_DIR", self.banner_dir),
            self.assertRaises(main.HTTPException) as raised,
        ):
            main.validate_composition_for_source(segment, 60.0)

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("заменён", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
