import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class BackendModuleBoundaryTests(unittest.TestCase):
    def test_request_models_live_outside_main(self):
        from schemas import AnalyzeRequest, ExportRequest, TranscribeRequest

        self.assertEqual(AnalyzeRequest(video_id="v").mode, "transcript")
        self.assertEqual(ExportRequest(video_id="v", segments=[]).mode, "separate")
        self.assertIsNone(TranscribeRequest(video_id="v").language)

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
