import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class BackendModuleBoundaryTests(unittest.TestCase):
    def test_request_models_live_outside_main(self):
        from schemas import AnalyzeRequest, ExportRequest, TranscribeRequest

        self.assertEqual(AnalyzeRequest(video_id="v").mode, "transcript")
        self.assertEqual(ExportRequest(video_id="v", segments=[]).mode, "separate")
        self.assertIsNone(TranscribeRequest(video_id="v").language)

    def test_export_request_has_safe_streamer_layout_defaults(self):
        from schemas import ExportRequest

        payload = ExportRequest(video_id="v", segments=[]).model_dump()

        self.assertEqual(payload["streamer_layout_enabled"], False)
        self.assertEqual(payload["webcam_region"], None)
        self.assertEqual(payload["gameplay_region"], None)
        self.assertEqual(payload["webcam_height_ratio"], 0.35)

    def test_streamer_layout_requires_two_bounded_crop_regions(self):
        from schemas import ExportRequest

        valid = {
            "video_id": "v",
            "segments": [],
            "aspect_ratio": "9:16",
            "streamer_layout_enabled": True,
            "webcam_region": {"x": 0.02, "y": 0.04, "width": 0.3, "height": 0.28},
            "gameplay_region": {"x": 0.18, "y": 0.1, "width": 0.72, "height": 0.84},
            "webcam_height_ratio": 0.38,
        }
        request = ExportRequest(**valid)
        self.assertEqual(request.webcam_region.model_dump(), valid["webcam_region"])

        invalid_cases = [
            {**valid, "webcam_region": None},
            {**valid, "gameplay_region": {"x": 0.8, "y": 0.1, "width": 0.3, "height": 0.5}},
            {**valid, "webcam_region": {"x": 0, "y": 0, "width": 0.01, "height": 0.2}},
            {**valid, "webcam_height_ratio": 0.8},
            {**valid, "aspect_ratio": "original"},
        ]
        for payload in invalid_cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    ExportRequest(**payload)

    def test_project_patch_rejects_invalid_streamer_geometry(self):
        from schemas import ProjectEditorPatch

        with self.assertRaises(ValidationError):
            ProjectEditorPatch(webcam_height_ratio=0.9)
        with self.assertRaises(ValidationError):
            ProjectEditorPatch(
                webcam_region={"x": 0.9, "y": 0, "width": 0.2, "height": 0.2},
            )

    def test_transcript_helpers_live_outside_main(self):
        from transcript_utils import extract_words_from_json, transcript_summary

        words = extract_words_from_json({
            "segments": [{"start": 0, "end": 2, "text": "два слова"}],
        })
        self.assertEqual([item["word"] for item in words], ["два", "слова"])
        self.assertEqual(
            transcript_summary({"full_text": "текст", "segments": [], "words": []})["preview"],
            "текст",
        )


if __name__ == "__main__":
    unittest.main()
