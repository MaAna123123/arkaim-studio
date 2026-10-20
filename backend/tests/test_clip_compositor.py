import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from clip_compositor import build_composition_filter, even_rect  # noqa: E402
from video_processor import VideoProcessor  # noqa: E402


def composition():
    return {
        "source_start": 0.0,
        "source_end": 10.0,
        "output_aspect_ratio": "9:16",
        "main_layer": {
            "crop": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.6},
            "destination": {"x": 0.0, "y": 0.25, "width": 1.0, "height": 0.75},
            "fit": "cover",
        },
    }


class ClipCompositorFilterTests(unittest.TestCase):
    def test_even_rect_rounds_and_clamps_to_valid_pixel_bounds(self):
        self.assertEqual(
            even_rect(
                {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.6},
                1919,
                1079,
            ),
            (192, 216, 1534, 646),
        )

    def test_main_crop_and_destination_are_placed_on_vertical_canvas(self):
        result = build_composition_filter(composition(), 1920, 1080, 1080, 1920, None, None, [])

        self.assertIn("crop=1536:648:192:216", result.filter_complex)
        self.assertIn("scale=1080:1440:force_original_aspect_ratio=increase", result.filter_complex)
        self.assertIn("overlay=x=0:y=480", result.filter_complex)
        self.assertEqual(result.video_map, "[composed]")
        self.assertEqual(result.overlay_inputs, ())

    def test_banner_and_overlay_ad_use_requested_fit_and_timing(self):
        value = composition()
        value["banner_layer"] = {
            "enabled": True,
            "destination": {"x": 0, "y": 0, "width": 1, "height": 0.25},
            "fit": "contain",
            "start": 0,
            "end": 6,
        }
        value["ad_layer"] = {
            "enabled": True,
            "mode": "overlay",
            "destination": {"x": 0.1, "y": 0.75, "width": 0.8, "height": 0.25},
            "fit": "stretch",
            "start": 2,
            "end": 8,
        }

        result = build_composition_filter(value, 1920, 1080, 1080, 1920, 1, 2, ["subtitles='captions.ass'"])

        self.assertEqual(result.overlay_inputs, ("banner", "ad"))
        self.assertIn("force_original_aspect_ratio=decrease", result.filter_complex)
        self.assertIn("pad=1080:480", result.filter_complex)
        self.assertIn("[2:v]setpts=PTS-STARTPTS,scale=864:480", result.filter_complex)
        self.assertIn("enable='between(t,0.000,6.000)'", result.filter_complex)
        self.assertIn("enable='between(t,2.000,8.000)'", result.filter_complex)
        self.assertIn("subtitles='captions.ass',format=yuv420p[composed]", result.filter_complex)

    def test_cover_overlay_uses_scale_then_crop_and_insert_ad_is_not_overlaid(self):
        value = composition()
        value["ad_layer"] = {
            "enabled": True,
            "mode": "insert",
            "destination": {"x": 0, "y": 0, "width": 1, "height": 0.3},
            "fit": "cover",
            "start": 0,
            "end": 10,
        }

        result = build_composition_filter(value, 1920, 1080, 1080, 1920, None, None, [])

        self.assertEqual(result.overlay_inputs, ())
        self.assertNotIn("[2:v]", result.filter_complex)


class CompositionCommandTests(unittest.TestCase):
    def test_composed_clip_loops_visual_inputs_and_keeps_only_source_audio(self):
        processor = VideoProcessor()
        value = composition()
        value["source_end"] = 4.0
        value["banner_layer"] = {
            "enabled": True,
            "destination": {"x": 0, "y": 0, "width": 1, "height": 0.25},
            "fit": "stretch", "start": 0, "end": 4,
        }
        value["ad_layer"] = {
            "enabled": True, "mode": "overlay",
            "destination": {"x": 0, "y": 0.75, "width": 1, "height": 0.25},
            "fit": "cover", "start": 1, "end": 3,
        }
        with tempfile.TemporaryDirectory(dir=BACKEND_DIR.parent / "temp") as temp_root:
            root = Path(temp_root)
            source, banner, ad = (root / name for name in ("source.mp4", "banner.mp4", "ad.mp4"))
            output = root / "output.mp4"
            for path in (source, banner, ad):
                path.write_bytes(b"media")
            commands = []

            def record(command):
                commands.append(command)
                output.write_bytes(b"rendered")
                return SimpleNamespace(stdout="", stderr="")

            with (
                patch.object(processor, "extract_metadata", return_value={
                    "duration": 12.0, "width": 1920, "height": 1080, "fps": 30,
                }),
                patch.object(processor, "_run", side_effect=record),
            ):
                processor.clip_video(
                    str(source), 0, 4, str(output), padding=0,
                    composition=value, video_banner_path=str(banner),
                    overlay_ad_path=str(ad), burn_subtitles=False,
                )

        command = commands[0]
        self.assertEqual(command.count("-stream_loop"), 2)
        self.assertIn("[composed]", command)
        self.assertIn("0:a?", command)
        self.assertNotIn("1:a", " ".join(command))
        self.assertNotIn("2:a", " ".join(command))


if __name__ == "__main__":
    unittest.main()
