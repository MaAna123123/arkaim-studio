import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from operation_manager import OperationControl  # noqa: E402
from transcriber import VideoTranscriber, merge_transcript_segments  # noqa: E402


def fake_segment(start, end, text):
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=[
            SimpleNamespace(
                word=text,
                start=start,
                end=end,
                probability=0.95,
            )
        ],
    )


class FakeModel:
    def __init__(self, segments):
        self.segments = segments
        self.kwargs = None

    def transcribe(self, _audio_path, **kwargs):
        self.kwargs = kwargs
        info = SimpleNamespace(
            language="ru",
            language_probability=0.98,
            duration=20.0,
        )
        return iter(self.segments), info


class ControlledTranscriber(VideoTranscriber):
    def __init__(self, model):
        super().__init__(model_size="tiny")
        self._model = model

    def _extract_audio(self, _video_path, out_path):
        Path(out_path).write_bytes(b"fake wav")
        return out_path


class TranscriberOperationTests(unittest.TestCase):
    def test_pause_waits_between_segments_and_resume_continues(self):
        model = FakeModel(
            [fake_segment(0.0, 2.0, "первый"), fake_segment(2.1, 4.0, "второй")]
        )
        transcriber = ControlledTranscriber(model)
        control = OperationControl()
        first_partial = threading.Event()
        release_callback = threading.Event()
        partial_count = []
        control_states = []
        paused_ack = threading.Event()
        result_holder = {}

        def on_control_state(state):
            control_states.append(state)
            if state == "paused":
                paused_ack.set()

        def on_partial(snapshot):
            partial_count.append(len(snapshot["segments"]))
            if len(partial_count) == 1:
                first_partial.set()
                release_callback.wait(timeout=1)

        worker = threading.Thread(
            target=lambda: result_holder.setdefault(
                "result",
                transcriber.transcribe(
                    "video.mp4",
                    control=control,
                    on_partial=on_partial,
                    on_control_state=on_control_state,
                ),
            )
        )
        worker.start()
        self.assertTrue(first_partial.wait(timeout=1))
        control.pause()
        release_callback.set()

        self.assertTrue(paused_ack.wait(timeout=1))
        self.assertTrue(worker.is_alive())
        self.assertEqual(partial_count, [1])

        control.resume()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(partial_count, [1, 2])
        self.assertEqual(control_states, ["paused", "running"])
        self.assertTrue(result_holder["result"]["is_complete"])

    def test_stop_returns_incomplete_result_with_checkpoint(self):
        model = FakeModel(
            [fake_segment(0.0, 2.0, "первый"), fake_segment(2.1, 4.0, "второй")]
        )
        transcriber = ControlledTranscriber(model)
        control = OperationControl()
        control_states = []

        def stop_after_first(_snapshot):
            control.request_stop()

        result = transcriber.transcribe(
            "video.mp4",
            control=control,
            on_partial=stop_after_first,
            on_control_state=control_states.append,
        )

        self.assertFalse(result["is_complete"])
        self.assertEqual(result["checkpoint"], 2.0)
        self.assertEqual([item["text"] for item in result["segments"]], ["первый"])
        self.assertEqual(control_states, ["stopped"])

    def test_resume_uses_one_second_overlap(self):
        model = FakeModel([fake_segment(10.2, 12.0, "продолжение")])
        transcriber = ControlledTranscriber(model)
        retained = {
            "segments": [
                {"start": 0.0, "end": 10.0, "text": "начало", "words": []}
            ]
        }

        result = transcriber.transcribe(
            "video.mp4", resume_from=10.0, retained_result=retained
        )

        self.assertEqual(model.kwargs["clip_timestamps"], [9.0])
        self.assertEqual(result["checkpoint"], 12.0)
        self.assertEqual(result["full_text"], "начало продолжение")

    def test_overlap_merge_removes_duplicate_boundary_segment(self):
        retained = [
            {"start": 0.0, "end": 2.0, "text": "привет", "words": []}
        ]
        resumed = [
            {"start": 1.5, "end": 2.2, "text": "привет", "words": []},
            {"start": 2.2, "end": 4.0, "text": "дальше", "words": []},
        ]

        merged = merge_transcript_segments(retained, resumed)

        self.assertEqual([item["text"] for item in merged], ["привет", "дальше"])

    def test_overlap_merge_preserves_new_tail_of_straddling_segment(self):
        retained = [
            {
                "start": 8.0,
                "end": 10.0,
                "text": "старая граница",
                "words": [
                    {"word": "старая", "start": 8.0, "end": 9.0},
                    {"word": "граница", "start": 9.0, "end": 10.0},
                ],
            }
        ]
        resumed = [
            {
                "start": 9.0,
                "end": 11.0,
                "text": "граница новая",
                "words": [
                    {"word": "граница", "start": 9.0, "end": 10.0},
                    {"word": "новая", "start": 10.0, "end": 11.0},
                ],
            }
        ]

        merged = merge_transcript_segments(retained, resumed)

        words = [word["word"] for segment in merged for word in segment["words"]]
        self.assertEqual(words, ["старая", "граница", "новая"])
        self.assertEqual(merged[-1]["start"], 10.0)
        self.assertEqual(merged[-1]["text"], "новая")

    def test_overlap_merge_trims_small_overlap_without_duplicate_words(self):
        retained = [
            {
                "start": 8.0,
                "end": 10.0,
                "text": "до границы",
                "words": [
                    {"word": "до", "start": 8.0, "end": 9.0},
                    {"word": "границы", "start": 9.0, "end": 10.0},
                ],
            }
        ]
        resumed = [
            {
                "start": 9.5,
                "end": 12.0,
                "text": "границы после",
                "words": [
                    {"word": "границы", "start": 9.5, "end": 10.0},
                    {"word": "после", "start": 10.0, "end": 12.0},
                ],
            }
        ]

        merged = merge_transcript_segments(retained, resumed)

        words = [word["word"] for segment in merged for word in segment["words"]]
        self.assertEqual(words, ["до", "границы", "после"])
        self.assertEqual(len([word for word in words if word == "границы"]), 1)

    def test_partial_callback_receives_immutable_snapshots(self):
        model = FakeModel([fake_segment(0.0, 2.0, "оригинал")])
        transcriber = ControlledTranscriber(model)

        def mutate_snapshot(snapshot):
            snapshot["segments"][0]["text"] = "испорчено"

        result = transcriber.transcribe("video.mp4", on_partial=mutate_snapshot)

        self.assertEqual(result["segments"][0]["text"], "оригинал")
        self.assertEqual(result["full_text"], "оригинал")


if __name__ == "__main__":
    unittest.main()
