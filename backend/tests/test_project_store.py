import json
import os
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
