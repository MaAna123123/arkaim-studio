import asyncio
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai_analyzer import VideoAIAnalyzer  # noqa: E402
from subtitle_parser import parse_srt_bytes  # noqa: E402
from video_processor import VideoProcessor  # noqa: E402


class SceneDetectionTests(unittest.TestCase):
    def test_reads_scene_times_emitted_by_current_ffmpeg_scdet(self):
        analyzer = VideoAIAnalyzer()
        ffmpeg_result = SimpleNamespace(
            stderr=(
                "[scdet @ 000] lavfi.scd.score: 15.093, lavfi.scd.time: 1.250\n"
                "[scdet @ 000] lavfi.scd.score: 20.000, lavfi.scd.time: 4.750\n"
            )
        )

        with patch("ai_analyzer.subprocess.run", return_value=ffmpeg_result):
            times = analyzer._detect_scene_changes("video.mp4")

        self.assertEqual(times, [1.25, 4.75])


class VisualProviderRoutingTests(unittest.TestCase):
    def test_non_gemini_api_key_is_never_sent_to_gemini_vision(self):
        analyzer = VideoAIAnalyzer(api_key="deepseek-secret", provider="deepseek")
        analyzer._analyze_gemini = AsyncMock(return_value=[])
        analyzer._analyze_local = AsyncMock(return_value=[{"source": "local"}])

        result = asyncio.run(analyzer.analyze("video.mp4", "find scenes"))

        analyzer._analyze_gemini.assert_not_awaited()
        analyzer._analyze_local.assert_awaited_once()
        self.assertEqual(result, [{"source": "local"}])


class SubtitleEncodingTests(unittest.TestCase):
    def test_utf16_srt_is_decoded(self):
        content = "1\n00:00:01,000 --> 00:00:03,000\nПривет мир\n".encode("utf-16")

        transcript = parse_srt_bytes(content, "utf16.srt")

        self.assertEqual(len(transcript["segments"]), 1)
        self.assertEqual(transcript["segments"][0]["text"], "Привет мир")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class SilentVideoProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=BACKEND_DIR.parent / "temp")
        self.root = Path(self.temp_dir.name)
        self.processor = VideoProcessor()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_video(self, path: Path, duration: float, size: str, color: str):
        subprocess.run(
            [
                "ffmpeg", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_video_banner_accepts_silent_source_clip(self):
        clip = self.root / "silent_clip.mp4"
        banner = self.root / "banner.mp4"
        output = self.root / "banner_output.mp4"
        self._make_video(clip, 2.0, "320x240", "blue")
        self._make_video(banner, 0.5, "120x60", "red")

        self.processor.insert_video_banner(str(clip), str(banner), str(output))

        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 0)

    def test_clip_video_accepts_silent_source(self):
        clip = self.root / "silent_source.mp4"
        output = self.root / "clipped_output.mp4"
        self._make_video(clip, 2.0, "320x240", "blue")

        self.processor.clip_video(
            str(clip),
            start=0.25,
            end=1.5,
            out_path=str(output),
            padding=0,
            aspect_ratio="original",
            burn_subtitles=False,
        )

        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 0)

    def test_ad_insertion_accepts_silent_source_clip(self):
        clip = self.root / "silent_clip.mp4"
        ad = self.root / "silent_ad.mp4"
        output = self.root / "ad_output.mp4"
        self._make_video(clip, 2.0, "320x240", "blue")
        self._make_video(ad, 0.5, "160x120", "red")

        self.processor.insert_ad_into_clip(str(clip), str(ad), str(output))

        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
