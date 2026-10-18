import sys
import tempfile
import unittest
import wave
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class AudioDependencyCompatibilityTests(unittest.TestCase):
    def test_faster_whisper_can_decode_project_wav(self):
        from faster_whisper.audio import decode_audio

        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "temp") as temp_dir:
            audio_path = Path(temp_dir) / "silence.wav"
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\x00\x00" * 1600)

            decoded = decode_audio(str(audio_path))

        self.assertEqual(decoded.shape, (1600,))

    def test_requirements_prevent_incompatible_pyav_19(self):
        requirements = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("av<19", requirements)


if __name__ == "__main__":
    unittest.main()
