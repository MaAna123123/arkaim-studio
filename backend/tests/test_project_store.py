import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from project_store import ProjectStore  # noqa: E402


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "workspace"
        self.project_root.mkdir()
        self.store = ProjectStore(self.project_root)
        self.video_id = "c4548a12-1ac7-4a30-95d7-36460a4eb456"

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_project(self, **overrides):
        args = {
            "video_id": self.video_id,
            "title": "Занятие 1",
            "source_filename": "lesson.mp4",
            "source_path": "backend/uploads/lesson.mp4",
            "video": {
                "duration": 123.4,
                "fps": 25.0,
                "width": 1920,
                "height": 1080,
                "size_bytes": 987654,
                "codec": "h264",
            },
        }
        args.update(overrides)
        return self.store.create_project(**args)

    def test_project_ids_must_be_uuids_before_any_path_is_created(self):
        for project_id in ("not-a-uuid", "../outside", "", "c4548a12"):
            with self.subTest(project_id=project_id):
                with self.assertRaises(ValueError):
                    self.store._project_dir(project_id)

        self.assertFalse((self.project_root / "data" / "projects").exists())

    def test_project_directory_is_confined_below_data_projects(self):
        directory = self.store._project_dir(self.video_id)

        self.assertEqual(directory, self.project_root / "data" / "projects" / self.video_id)
        self.assertTrue(directory.resolve().is_relative_to(
            (self.project_root / "data" / "projects").resolve()
        ))

    def test_create_project_writes_version_one_defaults_with_relative_source_path(self):
        project = self.create_project()
        saved = json.loads(
            (self.project_root / "data" / "projects" / self.video_id / "project.json")
            .read_text(encoding="utf-8")
        )

        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["project_id"], self.video_id)
        self.assertEqual(saved["video_id"], self.video_id)
        self.assertEqual(saved["title"], "Занятие 1")
        self.assertEqual(saved["source_filename"], "lesson.mp4")
        self.assertEqual(saved["source_path"], "backend/uploads/lesson.mp4")
        self.assertEqual(saved["video"], {
            "duration": 123.4,
            "fps": 25.0,
            "width": 1920,
            "height": 1080,
            "size_bytes": 987654,
            "codec": "h264",
        })
        self.assertEqual(saved["editor"], {
            "segments": [], "selected": [], "clip_results": [],
            "subtitle_context": None, "subtitle_notice": "", "export_padding": 0.5,
            "aspect_ratio": "9:16", "burn_subtitles": True, "insert_ad": False,
            "text_ad_enabled": False, "text_ad_text": "", "text_ad_position": "top",
            "text_ad_interval": 4, "text_ad_opacity": 0.75,
            "video_banner_enabled": False, "video_banner_height": 380,
        })
        self.assertTrue(saved["created_at"].endswith("Z"))
        self.assertEqual(saved["created_at"], saved["updated_at"])
        self.assertEqual(project, saved)

    def test_create_project_round_trips_utf8_title_and_safe_video_metadata(self):
        project = self.create_project(
            title="Привет, 世界 👋",
            video={
                "duration": 1.25,
                "thumbnail": "https://example.invalid/thumb.jpg",
                "uploader": "Автор",
                "view_count": 42,
                "chapters": [{"start_time": 0, "title": "Вступление"}],
                "has_chapters": True,
                "platform": "youtube",
                "has_subtitles": True,
                "subtitle_lang": "ru",
                "subtitle_is_auto": False,
                "unknown": "discard me",
            },
        )

        self.assertEqual(project["title"], "Привет, 世界 👋")
        self.assertEqual(project["video"], {
            "duration": 1.25,
            "thumbnail": "https://example.invalid/thumb.jpg",
            "uploader": "Автор",
            "view_count": 42,
            "chapters": [{"start_time": 0, "title": "Вступление"}],
            "has_chapters": True,
            "platform": "youtube",
            "has_subtitles": True,
            "subtitle_lang": "ru",
            "subtitle_is_auto": False,
        })

    def test_sensitive_and_unknown_video_metadata_never_persists_or_returns(self):
        project = self.create_project(video={
            "duration": 9,
            "api_key": "secret-api-key",
            "oauth_token": "oauth-secret",
            "subscription_url": "https://vpn.invalid/subscription",
            "vpn_credential": "vpn-password",
            "download_path": str(self.project_root / "backend" / "uploads" / "lesson.mp4"),
            "nested": {"api_key": "still-secret"},
        })
        saved_text = (
            self.project_root / "data" / "projects" / self.video_id / "project.json"
        ).read_text(encoding="utf-8")
        loaded = self.store.get_project(self.video_id)

        self.assertEqual(project["video"], {"duration": 9})
        self.assertEqual(loaded["video"], {"duration": 9})
        for forbidden in (
            "secret-api-key", "oauth-secret", "subscription", "vpn-password",
            str(self.project_root), "nested",
        ):
            self.assertNotIn(forbidden, saved_text)
            self.assertNotIn(forbidden, json.dumps(loaded, ensure_ascii=False))

    def test_subscription_like_value_in_allowed_metadata_is_not_persisted(self):
        project = self.create_project(video={
            "duration": 9,
            "thumbnail": "https://vpn.invalid/subscription/secret-token",
        })

        self.assertEqual(project["video"], {"duration": 9})

    def test_tokenized_url_in_allowed_metadata_is_not_persisted(self):
        project = self.create_project(video={
            "duration": 9,
            "thumbnail": "https://vpn.invalid/s/secret-token",
        })

        self.assertEqual(project["video"], {"duration": 9})

    def test_source_filename_never_returns_an_absolute_path(self):
        project = self.create_project(
            source_filename="E:/private/media/lesson.mp4",
        )

        self.assertEqual(project["source_filename"], "lesson.mp4")

    def test_create_project_rejects_source_path_outside_project_root(self):
        with self.assertRaises(ValueError):
            self.create_project(source_path="../outside/lesson.mp4")

        with self.assertRaises(ValueError):
            self.create_project(source_path=Path(self.temp_dir.name) / "lesson.mp4")

    def test_get_project_drops_tampered_relative_source_path_outside_project_root(self):
        self.create_project()
        project_file = (
            self.project_root / "data" / "projects" / self.video_id / "project.json"
        )
        tampered = json.loads(project_file.read_text(encoding="utf-8"))
        tampered["source_path"] = "../../outside/secret.mp4"
        project_file.write_text(json.dumps(tampered), encoding="utf-8")

        loaded = self.store.get_project(self.video_id)

        self.assertNotIn("source_path", loaded)

    def test_atomic_write_uses_a_unique_same_directory_temp_file_and_fsync(self):
        destination = self.project_root / "data" / "projects" / self.video_id / "project.json"
        observed = []
        real_replace = os.replace

        def recording_replace(source, target):
            observed.append((Path(source), Path(target)))
            return real_replace(source, target)

        with patch("project_store.os.fsync", wraps=os.fsync) as fsync, patch(
            "project_store.os.replace", side_effect=recording_replace
        ) as replace:
            self.store._atomic_write_json(destination, {"title": "данные"})

        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"title": "данные"})
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(fsync.call_count, 1)
        self.assertEqual(observed[0][0].parent, destination.parent)
        self.assertNotEqual(observed[0][0], destination)
        self.assertEqual(observed[0][1], destination)
        self.assertEqual(list(destination.parent.glob("*.tmp-*")), [])

    def test_atomic_write_cleans_temporary_file_and_preserves_previous_file_on_replace_error(self):
        destination = self.project_root / "data" / "projects" / self.video_id / "project.json"
        destination.parent.mkdir(parents=True)
        destination.write_text('{"title":"previous"}', encoding="utf-8")

        with patch("project_store.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.store._atomic_write_json(destination, {"title": "new"})

        self.assertEqual(destination.read_text(encoding="utf-8"), '{"title":"previous"}')
        self.assertEqual(list(destination.parent.glob("*.tmp-*")), [])

    def test_update_editor_updates_only_allowlisted_fields_and_preserves_other_editor_state(self):
        self.create_project()
        with patch.object(self.store, "_utc_now", return_value="2026-10-17T12:00:00Z"):
            updated = self.store.update_editor(self.video_id, {
                "segments": [{"start": 0, "end": 12}],
                "export_padding": 0.75,
            })

        saved = self.store.get_project(self.video_id)
        self.assertEqual(updated["editor"]["segments"], [{"start": 0, "end": 12}])
        self.assertEqual(saved["editor"]["segments"], [{"start": 0, "end": 12}])
        self.assertEqual(saved["editor"]["export_padding"], 0.75)
        self.assertEqual(saved["editor"]["aspect_ratio"], "9:16")
        self.assertEqual(saved["editor"]["selected"], [])
        self.assertEqual(saved["updated_at"], "2026-10-17T12:00:00Z")

    def test_update_editor_rejects_unknown_path_and_identity_fields(self):
        self.create_project()
        for patch_data in (
            {"unknown": True},
            {"source_path": "../../private.mp4"},
            {"project_id": "c4548a12-1ac7-4a30-95d7-36460a4eb456"},
            {"video_id": "c4548a12-1ac7-4a30-95d7-36460a4eb456"},
        ):
            with self.subTest(patch_data=patch_data):
                with self.assertRaises(ValueError):
                    self.store.update_editor(self.video_id, patch_data)

        self.assertEqual(self.store.get_project(self.video_id)["editor"], {
            "segments": [], "selected": [], "clip_results": [],
            "subtitle_context": None, "subtitle_notice": "", "export_padding": 0.5,
            "aspect_ratio": "9:16", "burn_subtitles": True, "insert_ad": False,
            "text_ad_enabled": False, "text_ad_text": "", "text_ad_position": "top",
            "text_ad_interval": 4, "text_ad_opacity": 0.75,
            "video_banner_enabled": False, "video_banner_height": 380,
        })

    def test_update_editor_strips_nested_credentials_and_absolute_paths_but_keeps_safe_context(self):
        self.create_project()
        unsafe_path = str(self.project_root / "private" / "subtitle-context.json")
        updated = self.store.update_editor(self.video_id, {
            "subtitle_context": {
                "language": "ru",
                "speakers": {"0": "Ведущий"},
                "api_key": "secret-api-key",
                "cache_path": unsafe_path,
                "nested": {
                    "style": "caption",
                    "oauth_token": "oauth-secret",
                    "source_file": unsafe_path,
                },
            },
            "segments": [{"start": 0, "end": 12, "text": "Безопасный текст"}],
        })
        project_file = self.store._project_dir(self.video_id) / "project.json"
        saved_text = project_file.read_text(encoding="utf-8")

        self.assertEqual(updated["editor"]["subtitle_context"], {
            "language": "ru",
            "speakers": {"0": "Ведущий"},
            "nested": {"style": "caption"},
        })
        self.assertEqual(updated["editor"]["segments"], [
            {"start": 0, "end": 12, "text": "Безопасный текст"},
        ])
        for forbidden in ("secret-api-key", "oauth-secret", unsafe_path):
            self.assertNotIn(forbidden, saved_text)
            self.assertNotIn(forbidden, json.dumps(updated, ensure_ascii=False))

    def test_list_projects_sorts_bounded_summaries_without_deleting_projects(self):
        source = self.project_root / "backend" / "uploads" / "lesson.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")
        project_ids = [str(uuid.UUID(int=index)) for index in range(1, 22)]
        for project_id in project_ids:
            self.create_project(video_id=project_id)

        newest_id, middle_id, missing_id = project_ids[:3]
        newest_file = self.store._project_dir(newest_id) / "project.json"
        newest = json.loads(newest_file.read_text(encoding="utf-8"))
        newest["updated_at"] = "2099-10-17T13:00:00Z"
        newest["editor"]["clip_results"] = [{"clip": 1}, {"clip": 2}]
        newest_file.write_text(json.dumps(newest), encoding="utf-8")
        (self.store._project_dir(newest_id) / "transcript.json").write_text(
            json.dumps({"full_text": "готово"}), encoding="utf-8"
        )

        middle_file = self.store._project_dir(middle_id) / "project.json"
        middle = json.loads(middle_file.read_text(encoding="utf-8"))
        middle["updated_at"] = "2099-10-17T12:00:00Z"
        middle_file.write_text(json.dumps(middle), encoding="utf-8")
        (self.store._project_dir(middle_id) / "checkpoint.json").write_text(json.dumps({
            "schema_version": 1,
            "operation_id": str(uuid.UUID(int=99)),
            "operation_type": "transcription",
            "video_id": middle_id,
            "status": "stopped",
            "progress_pct": 37,
            "partial_result": {"checkpoint": 412.8},
        }), encoding="utf-8")

        missing_file = self.store._project_dir(missing_id) / "project.json"
        missing = json.loads(missing_file.read_text(encoding="utf-8"))
        missing["updated_at"] = "2099-10-17T11:00:00Z"
        missing["source_path"] = "backend/uploads/missing.mp4"
        missing_file.write_text(json.dumps(missing), encoding="utf-8")

        listing = self.store.list_projects(limit=999)

        self.assertEqual(len(listing["projects"]), 20)
        self.assertEqual([item["project_id"] for item in listing["projects"][:3]], [
            newest_id, middle_id, missing_id,
        ])
        self.assertTrue((self.store._project_dir(project_ids[-1]) / "project.json").exists())
        self.assertEqual(listing["projects"][0], {
            "project_id": newest_id,
            "video_id": newest_id,
            "title": "Занятие 1",
            "filename": "lesson.mp4",
            "updated_at": "2099-10-17T13:00:00Z",
            "duration": 123.4,
            "source_exists": True,
            "has_transcript": True,
            "clips_count": 2,
            "transcription": None,
        })
        self.assertEqual(listing["projects"][1]["transcription"], {
            "operation_id": str(uuid.UUID(int=99)),
            "status": "stopped",
            "progress_pct": 37,
            "checkpoint": 412.8,
        })
        self.assertFalse(listing["projects"][2]["source_exists"])
        self.assertEqual(self.store.source_status(newest_id), {
            "exists": True,
            "diagnostic": None,
        })
        self.assertEqual(self.store.source_status(missing_id), {
            "exists": False,
            "diagnostic": {
                "code": "source_missing",
                "message": "Исходный файл не найден",
            },
        })

    def test_list_projects_skips_malformed_project_without_rewriting_it(self):
        healthy_id = self.video_id
        malformed_id = str(uuid.UUID(int=42))
        self.create_project(video_id=healthy_id)
        malformed_file = self.store._project_dir(malformed_id) / "project.json"
        malformed_file.parent.mkdir(parents=True)
        malformed_text = '{"schema_version": 1, broken'
        malformed_file.write_text(malformed_text, encoding="utf-8")

        listing = self.store.list_projects()

        self.assertEqual([item["project_id"] for item in listing["projects"]], [healthy_id])
        self.assertEqual(listing["diagnostics"], [{
            "code": "project_unreadable",
            "project_id": malformed_id,
            "message": "invalid project JSON",
        }])
        self.assertEqual(malformed_file.read_text(encoding="utf-8"), malformed_text)

    def test_list_projects_keeps_project_visible_when_checkpoint_is_malformed(self):
        self.create_project()
        checkpoint_file = self.store._project_dir(self.video_id) / "checkpoint.json"
        malformed_text = '{"operation_id": broken'
        checkpoint_file.write_text(malformed_text, encoding="utf-8")

        listing = self.store.list_projects()

        self.assertEqual(len(listing["projects"]), 1)
        self.assertEqual(listing["projects"][0]["project_id"], self.video_id)
        self.assertEqual(listing["projects"][0]["transcription"], {
            "resume_available": False,
            "diagnostic": {
                "code": "checkpoint_unreadable",
                "message": "Не удалось прочитать сохранённый прогресс",
            },
        })
        self.assertEqual(listing["diagnostics"], [{
            "code": "checkpoint_unreadable",
            "project_id": self.video_id,
            "message": "Не удалось прочитать сохранённый прогресс",
        }])
        self.assertEqual(checkpoint_file.read_text(encoding="utf-8"), malformed_text)

    def test_list_projects_disables_resume_for_parseable_incomplete_checkpoint(self):
        self.create_project()
        checkpoint_file = self.store._project_dir(self.video_id) / "checkpoint.json"
        malformed_checkpoint = {}
        checkpoint_file.write_text(json.dumps(malformed_checkpoint), encoding="utf-8")
        original_checkpoint = checkpoint_file.read_text(encoding="utf-8")

        listing = self.store.list_projects()

        self.assertEqual(len(listing["projects"]), 1)
        self.assertEqual(listing["projects"][0]["transcription"], {
            "resume_available": False,
            "diagnostic": {
                "code": "checkpoint_invalid",
                "message": "Сохранённый прогресс нельзя продолжить",
            },
        })
        self.assertEqual(listing["diagnostics"], [{
            "code": "checkpoint_invalid",
            "project_id": self.video_id,
            "message": "Сохранённый прогресс нельзя продолжить",
        }])
        self.assertEqual(checkpoint_file.read_text(encoding="utf-8"), original_checkpoint)

    def test_unsupported_project_schema_is_reported_without_rewriting_project_or_checkpoint(self):
        self.create_project()
        project_file = self.store._project_dir(self.video_id) / "project.json"
        checkpoint_file = self.store._project_dir(self.video_id) / "checkpoint.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        project["schema_version"] = 2
        project_file.write_text(json.dumps(project), encoding="utf-8")
        checkpoint_file.write_text(json.dumps({"schema_version": 1, "status": "stopped"}), encoding="utf-8")
        original_project = project_file.read_text(encoding="utf-8")
        original_checkpoint = checkpoint_file.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "unsupported project schema"):
            self.store.get_project(self.video_id)
        listing = self.store.list_projects()

        self.assertEqual(listing, {
            "projects": [],
            "diagnostics": [{
                "code": "project_schema_unsupported",
                "project_id": self.video_id,
                "message": "Неподдерживаемая версия проекта",
            }],
        })
        self.assertEqual(project_file.read_text(encoding="utf-8"), original_project)
        self.assertEqual(checkpoint_file.read_text(encoding="utf-8"), original_checkpoint)

    def test_mismatched_project_uuid_is_rejected_and_reported_without_rewriting_record(self):
        self.create_project()
        project_file = self.store._project_dir(self.video_id) / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        mismatched_id = str(uuid.UUID(int=61))
        project["project_id"] = mismatched_id
        project["video_id"] = mismatched_id
        project_file.write_text(json.dumps(project), encoding="utf-8")
        original_project = project_file.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "project identity"):
            self.store.get_project(self.video_id)
        self.assertEqual(self.store.list_projects(), {
            "projects": [],
            "diagnostics": [{
                "code": "project_identity_invalid",
                "project_id": self.video_id,
                "message": "Недопустимая идентичность проекта",
            }],
        })
        self.assertEqual(project_file.read_text(encoding="utf-8"), original_project)

    def test_non_uuid_video_id_is_rejected_and_reported_without_rewriting_record(self):
        self.create_project()
        project_file = self.store._project_dir(self.video_id) / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        project["video_id"] = "legacy-video-name"
        project_file.write_text(json.dumps(project), encoding="utf-8")
        original_project = project_file.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "project identity"):
            self.store.get_project(self.video_id)
        self.assertEqual(self.store.list_projects(), {
            "projects": [],
            "diagnostics": [{
                "code": "project_identity_invalid",
                "project_id": self.video_id,
                "message": "Недопустимая идентичность проекта",
            }],
        })
        self.assertEqual(project_file.read_text(encoding="utf-8"), original_project)

    def test_missing_source_reports_status_without_changing_project_or_checkpoint(self):
        self.create_project(source_path="backend/uploads/missing.mp4")
        project_file = self.store._project_dir(self.video_id) / "project.json"
        checkpoint_file = self.store._project_dir(self.video_id) / "checkpoint.json"
        checkpoint_file.write_text(json.dumps({"schema_version": 1, "status": "stopped"}), encoding="utf-8")
        original_project = project_file.read_text(encoding="utf-8")
        original_checkpoint = checkpoint_file.read_text(encoding="utf-8")

        status = self.store.source_status(self.video_id)
        listing = self.store.list_projects()

        self.assertEqual(status["exists"], False)
        self.assertFalse(listing["projects"][0]["source_exists"])
        self.assertEqual(project_file.read_text(encoding="utf-8"), original_project)
        self.assertEqual(checkpoint_file.read_text(encoding="utf-8"), original_checkpoint)

    def test_checkpoint_and_final_transcript_round_trip_with_checkpoint_removal(self):
        self.create_project()
        checkpoint = {
            "schema_version": 1,
            "operation_id": str(uuid.UUID(int=88)),
            "operation_type": "transcription",
            "video_id": self.video_id,
            "status": "stopped",
            "progress_pct": 37,
            "partial_result": {"checkpoint": 412.8, "segments": [{"text": "часть"}]},
        }
        transcript = {
            "full_text": "Готовый текст",
            "segments": [{"text": "Готовый текст"}],
            "duration": 123.4,
        }

        saved_checkpoint = self.store.save_checkpoint(self.video_id, checkpoint)
        loaded_checkpoint = self.store.load_checkpoint(self.video_id)
        saved_transcript = self.store.save_transcript(self.video_id, transcript)
        loaded_transcript = self.store.load_transcript(self.video_id)

        self.assertEqual(saved_checkpoint, checkpoint)
        self.assertEqual(loaded_checkpoint, checkpoint)
        self.assertEqual(saved_transcript, transcript)
        self.assertEqual(loaded_transcript, transcript)
        self.assertTrue(self.store.remove_checkpoint(self.video_id))
        self.assertIsNone(self.store.load_checkpoint(self.video_id))
        self.assertFalse(self.store.remove_checkpoint(self.video_id))

    def test_checkpoint_write_is_skipped_when_writer_loses_ownership(self):
        self.create_project()
        retained = {"operation_id": "retained", "status": "stopped"}
        stale = {"operation_id": "stale", "status": "stopped"}
        self.store.save_checkpoint(self.video_id, retained)

        saved = self.store.save_checkpoint(
            self.video_id, stale, is_current=lambda: False
        )

        self.assertIsNone(saved)
        self.assertEqual(self.store.load_checkpoint(self.video_id), retained)

    def test_final_transcript_strips_incomplete_fields_from_saved_and_loaded_payloads(self):
        self.create_project()
        transcript = {
            "full_text": "Готовый текст",
            "segments": [{"text": "Готовый текст"}],
            "duration": 123.4,
            "checkpoint": 412.8,
            "is_complete": True,
        }

        saved = self.store.save_transcript(self.video_id, transcript)
        loaded = self.store.load_transcript(self.video_id)
        on_disk = json.loads(
            (self.store._project_dir(self.video_id) / "transcript.json").read_text(encoding="utf-8")
        )

        expected = {
            "full_text": "Готовый текст",
            "segments": [{"text": "Готовый текст"}],
            "duration": 123.4,
        }
        self.assertEqual(saved, expected)
        self.assertEqual(loaded, expected)
        self.assertEqual(on_disk, expected)

    def test_opening_project_copies_legacy_transcript_without_deleting_legacy_file(self):
        legacy_dir = self.project_root / "backend" / "transcripts"
        legacy_dir.mkdir(parents=True)
        self.store = ProjectStore(self.project_root, legacy_transcripts_dir=legacy_dir)
        self.create_project()
        legacy_file = legacy_dir / f"{self.video_id}.json"
        legacy_transcript = {"full_text": "Старый готовый текст", "segments": [{"text": "Старый"}]}
        legacy_file.write_text(json.dumps(legacy_transcript, ensure_ascii=False), encoding="utf-8")

        self.store.get_project(self.video_id)

        copied_file = self.store._project_dir(self.video_id) / "transcript.json"
        self.assertEqual(self.store.load_transcript(self.video_id), legacy_transcript)
        self.assertEqual(json.loads(copied_file.read_text(encoding="utf-8")), legacy_transcript)
        self.assertEqual(json.loads(legacy_file.read_text(encoding="utf-8")), legacy_transcript)

    def test_legacy_transcript_copy_strips_incomplete_fields_without_rewriting_legacy_file(self):
        legacy_dir = self.project_root / "backend" / "transcripts"
        legacy_dir.mkdir(parents=True)
        self.store = ProjectStore(self.project_root, legacy_transcripts_dir=legacy_dir)
        self.create_project()
        legacy_file = legacy_dir / f"{self.video_id}.json"
        legacy_transcript = {
            "full_text": "Старый готовый текст",
            "checkpoint": 8.5,
            "is_complete": True,
        }
        legacy_file.write_text(json.dumps(legacy_transcript, ensure_ascii=False), encoding="utf-8")

        loaded = self.store.load_transcript(self.video_id)
        copied = json.loads(
            (self.store._project_dir(self.video_id) / "transcript.json").read_text(encoding="utf-8")
        )

        self.assertEqual(loaded, {"full_text": "Старый готовый текст"})
        self.assertEqual(copied, {"full_text": "Старый готовый текст"})
        self.assertEqual(json.loads(legacy_file.read_text(encoding="utf-8")), legacy_transcript)


if __name__ == "__main__":
    unittest.main()
