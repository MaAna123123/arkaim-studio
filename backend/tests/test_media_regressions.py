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

    def test_ass_subtitles_dedupe_overlapping_words_and_dialogue_windows(self):
        subtitle_path = Path(self.processor._create_ass_subtitles(
            [
                {"word": "Привет", "start": 0.0, "end": 0.6},
                {"word": "Привет", "start": 0.1, "end": 0.65},
                {"word": "это", "start": 0.55, "end": 0.9},
                {"word": "тест", "start": 0.85, "end": 1.2},
                {"word": "дальше", "start": 1.0, "end": 1.4},
            ],
            0,
            2,
            play_res_x=1080,
            play_res_y=1920,
        ))
        try:
            content = subtitle_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("Привет"), 1)
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1920", content)
            dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]
            self.assertEqual(len(dialogue_lines), 2)
            first_end = dialogue_lines[0].split(",", 3)[2]
            second_start = dialogue_lines[1].split(",", 3)[1]
            self.assertLessEqual(first_end, second_start)
        finally:
            subtitle_path.unlink(missing_ok=True)

    def test_streamer_layout_stacks_selected_webcam_above_selected_gameplay(self):
        source = self.root / "streamer_source.mp4"
        output = self.root / "streamer_short.mp4"
        subprocess.run(
            [
                "ffmpeg", "-f", "lavfi", "-i",
                "color=c=blue:s=640x360:d=1,drawbox=x=0:y=0:w=160:h=120:color=red:t=fill",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(source),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        try:
            self.processor.clip_video(
                str(source),
                start=0,
                end=0.8,
                out_path=str(output),
                padding=0,
                aspect_ratio="9:16",
                burn_subtitles=False,
                streamer_layout_enabled=True,
                webcam_region={"x": 0, "y": 0, "width": 0.25, "height": 1 / 3},
                gameplay_region={"x": 0.25, "y": 0, "width": 0.75, "height": 1},
                webcam_height_ratio=0.35,
            )
        except TypeError as exc:
            self.fail(f"clip_video does not expose streamer layout yet: {exc}")

        metadata = self.processor.extract_metadata(str(output))
        self.assertEqual((metadata["width"], metadata["height"]), (1080, 1920))
        frame = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        top = (150 * 1080 + 540) * 3
        bottom = (1400 * 1080 + 540) * 3
        top_rgb = frame[top:top + 3]
        bottom_rgb = frame[bottom:bottom + 3]
        self.assertGreater(top_rgb[0], top_rgb[2] + 100)
        self.assertGreater(bottom_rgb[2], bottom_rgb[0] + 100)

    def test_streamer_layout_uses_display_dimensions_for_rotated_phone_video(self):
        base = self.root / "phone_base.mp4"
        source = self.root / "phone_rotated.mp4"
        output = self.root / "phone_short.mp4"
        self._make_video(base, 1.0, "320x240", "blue")
        subprocess.run(
            [
                "ffmpeg", "-display_rotation", "90", "-i", str(base),
                "-c", "copy", "-y", str(source),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        metadata = self.processor.extract_metadata(str(source))
        self.assertEqual((metadata["width"], metadata["height"]), (240, 320))
        self.processor.clip_video(
            str(source),
            start=0,
            end=0.8,
            out_path=str(output),
            padding=0,
            aspect_ratio="9:16",
            burn_subtitles=False,
            streamer_layout_enabled=True,
            webcam_region={"x": 0, "y": 0, "width": 1, "height": 0.3},
            gameplay_region={"x": 0, "y": 0.3, "width": 1, "height": 0.7},
            webcam_height_ratio=0.35,
        )
        rendered = self.processor.extract_metadata(str(output))
        self.assertEqual((rendered["width"], rendered["height"]), (1080, 1920))

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
