import io
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
from youtube_downloader import _parse_vtt_or_xml  # noqa: E402
from social_uploader import YouTubeUploader  # noqa: E402


class TranscriptSecurityTests(unittest.TestCase):
    def test_transcript_route_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory(dir=main.TEMP_DIR) as temp_root:
            root = Path(temp_root)
            transcripts = root / "transcripts"
            transcripts.mkdir()
            (root / "sentinel.json").write_text('{"leaked": true}', encoding="utf-8")

            with patch.object(main, "TRANSCRIPTS_DIR", transcripts):
                response = TestClient(main.app).get(
                    "/api/video/transcript/..%5Csentinel"
                )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("leaked", response.text)


class CorsSecurityTests(unittest.TestCase):
    def test_untrusted_web_origin_is_not_allowed(self):
        response = TestClient(main.app).options(
            "/api/settings",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
            },
        )

        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_loopback_frontend_origin_is_allowed(self):
        response = TestClient(main.app).options(
            "/api/settings",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
            },
        )

        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://127.0.0.1:5173",
        )


class SubtitleParserTests(unittest.TestCase):
    def test_xml_dur_is_added_to_start_even_when_larger_than_start(self):
        payload = (
            b'<transcript><text start="1" dur="5">hello world</text></transcript>'
        )

        segments, _ = _parse_vtt_or_xml(payload, "srv1")

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start"], 1.0)
        self.assertEqual(segments[0]["end"], 6.0)

    def test_xml_end_is_used_as_absolute_time(self):
        payload = b'<tt><p begin="00:00:02.000" end="00:00:07.000">text</p></tt>'

        segments, _ = _parse_vtt_or_xml(payload, "ttml")

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start"], 2.0)
        self.assertEqual(segments[0]["end"], 7.0)


class ExportValidationTests(unittest.TestCase):
    def test_nan_timestamps_are_rejected_before_export(self):
        fake_export = AsyncMock(return_value=[])
        with (
            patch.object(main, "_find_video", return_value="video.mp4"),
            patch.object(main, "_export_segments", fake_export),
        ):
            response = TestClient(main.app).post(
                "/api/video/export",
                json={
                    "video_id": "00000000-0000-0000-0000-000000000000",
                    "segments": [{"start": "NaN", "end": "NaN"}],
                },
            )

        self.assertEqual(response.status_code, 400)
        fake_export.assert_not_awaited()


class YoutubeCallbackTests(unittest.TestCase):
    def test_uploader_rejects_mismatched_oauth_state_before_token_exchange(self):
        with tempfile.TemporaryDirectory(dir=main.TEMP_DIR) as temp_root:
            secret_file = Path(temp_root) / "client_secret.json"
            secret_file.write_text("{}", encoding="utf-8")
            uploader = YouTubeUploader()
            uploader.CLIENT_SECRET_FILE = secret_file
            uploader._oauth_state = "expected-state"

            success = uploader.handle_callback("authorization-code", "attacker-state")

        self.assertFalse(success)

    def test_callback_requires_oauth_state(self):
        with patch.object(main.youtube_uploader, "handle_callback", return_value=True) as callback:
            response = TestClient(main.app).get(
                "/api/social/youtube/callback?code=authorization-code"
            )

        self.assertEqual(response.status_code, 400)
        callback.assert_not_called()

    def test_success_callback_returns_renderable_html(self):
        with patch.object(main.youtube_uploader, "handle_callback", return_value=True):
            response = TestClient(main.app).get(
                "/api/social/youtube/callback?code=authorization-code&state=expected-state"
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertTrue(response.text.lstrip().startswith("<html>"))
        self.assertFalse(response.content.startswith(b'"'))


class AssetReplacementTests(unittest.TestCase):
    def test_invalid_replacement_keeps_previous_ad_and_banner(self):
        client = TestClient(main.app)

        for route, directory_attr, current_name in (
            ("/api/ad/upload", "ADS_DIR", "current_ad.mp4"),
            ("/api/banner/upload", "BANNER_DIR", "current_banner.mp4"),
        ):
            with self.subTest(route=route):
                with tempfile.TemporaryDirectory(dir=main.TEMP_DIR) as temp_root:
                    directory = Path(temp_root)
                    previous = directory / current_name
                    previous.write_bytes(b"known-good-video")

                    with (
                        patch.object(main, directory_attr, directory),
                        patch.object(
                            main.processor,
                            "extract_metadata",
                            side_effect=RuntimeError("invalid video"),
                        ),
                    ):
                        response = client.post(
                            route,
                            files={"file": ("replacement.mov", b"not-a-video", "video/quicktime")},
                        )

                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(previous.read_bytes(), b"known-good-video")


if __name__ == "__main__":
    unittest.main()
