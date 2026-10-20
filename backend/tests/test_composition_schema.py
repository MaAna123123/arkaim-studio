import math
import unittest

from pydantic import ValidationError

from schemas import ClipComposition, ExportRequest, ExportSegment


def composition_payload():
    return {
        "source_start": 12.5,
        "source_end": 48.2,
        "output_aspect_ratio": "9:16",
        "main_layer": {
            "crop": {"x": 0, "y": 0, "width": 1, "height": 1},
            "destination": {"x": 0, "y": 0.25, "width": 1, "height": 0.75},
            "fit": "cover",
        },
        "banner_layer": {
            "enabled": True,
            "destination": {"x": 0, "y": 0, "width": 1, "height": 0.25},
            "fit": "stretch",
            "start": 0,
            "end": 12,
        },
        "ad_layer": {
            "enabled": True,
            "mode": "overlay",
            "destination": {"x": 0, "y": 0, "width": 1, "height": 0.25},
            "fit": "cover",
            "start": 12,
            "end": 18,
        },
    }


class CompositionSchemaTests(unittest.TestCase):
    def test_accepts_normalized_composition_and_export_segment(self):
        segment = ExportSegment(
            start=12.5,
            end=48.2,
            label="Moment",
            composition=composition_payload(),
        )
        self.assertEqual(segment.composition.ad_layer.mode, "overlay")
        request = ExportRequest(video_id="video", segments=[segment])
        self.assertEqual(len(request.segments), 1)

    def test_rejects_non_finite_and_out_of_bounds_rectangles(self):
        for bad_value in (math.nan, math.inf, -math.inf):
            payload = composition_payload()
            payload["main_layer"]["crop"]["x"] = bad_value
            with self.assertRaises(ValidationError):
                ClipComposition(**payload)

        payload = composition_payload()
        payload["main_layer"]["crop"]["width"] = 0.02
        with self.assertRaises(ValidationError):
            ClipComposition(**payload)

    def test_rejects_crossed_source_and_overlay_ranges(self):
        payload = composition_payload()
        payload["source_end"] = payload["source_start"]
        with self.assertRaises(ValidationError):
            ClipComposition(**payload)

        payload = composition_payload()
        payload["banner_layer"]["end"] = 50
        with self.assertRaises(ValidationError):
            ClipComposition(**payload)

    def test_rejects_unknown_fit_and_ad_mode(self):
        payload = composition_payload()
        payload["banner_layer"]["fit"] = "unknown"
        with self.assertRaises(ValidationError):
            ClipComposition(**payload)

        payload = composition_payload()
        payload["ad_layer"]["mode"] = "replace"
        with self.assertRaises(ValidationError):
            ClipComposition(**payload)

    def test_accepts_midpoint_insert_mode(self):
        payload = composition_payload()
        payload["ad_layer"]["mode"] = "insert"
        model = ClipComposition(**payload)
        self.assertEqual(model.ad_layer.mode, "insert")

    def test_requires_public_segment_times_to_match_composition(self):
        with self.assertRaises(ValidationError):
            ExportSegment(start=10, end=48.2, composition=composition_payload())


if __name__ == "__main__":
    unittest.main()
