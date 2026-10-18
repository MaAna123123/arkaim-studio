"""
transcriber.py — локальная транскрипция видео через faster-whisper.

Как работает:
  1. Извлекаем аудио из видео через FFmpeg (быстро, без потери качества)
  2. Запускаем faster-whisper — локальная ИИ-модель, работает без интернета
  3. Получаем полный текст + таймкод каждого слова
  4. Формируем "параграфы" — логические блоки текста с паузами между ними

Почему faster-whisper, а не обычный Whisper:
  - Работает без PyTorch (через CTranslate2) — легче установить
  - В 2–4× быстрее оригинального Whisper
  - Такое же качество распознавания
"""

import copy
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

try:
    from .operation_manager import OperationControl
except ImportError:
    from operation_manager import OperationControl


def merge_transcript_segments(
    retained: list[dict],
    resumed: list[dict],
) -> list[dict]:
    """Merge resumed output without losing a segment tail at the checkpoint."""
    merged = copy.deepcopy(retained)
    checkpoint = max(
        (float(segment.get("end", 0.0)) for segment in retained),
        default=0.0,
    )
    epsilon = 0.02
    for candidate in resumed:
        candidate = copy.deepcopy(candidate)
        start = float(candidate.get("start", 0.0))
        end = float(candidate.get("end", start))

        words = candidate.get("words") or []
        if words and start < checkpoint + epsilon:
            kept_words = [
                copy.deepcopy(word)
                for word in words
                if float(word.get("end", word.get("start", 0.0))) > checkpoint + epsilon
            ]
            if not kept_words:
                continue
            candidate["words"] = kept_words
            candidate["start"] = float(kept_words[0].get("start", checkpoint))
            candidate["end"] = float(kept_words[-1].get("end", candidate["start"]))
            candidate["text"] = " ".join(
                str(word.get("word", "")).strip()
                for word in kept_words
                if str(word.get("word", "")).strip()
            )
            start = float(candidate["start"])
            end = float(candidate["end"])

        duration = max(end - start, 0.001)
        duplicate = False
        for existing in merged:
            existing_start = float(existing.get("start", 0.0))
            existing_end = float(existing.get("end", existing_start))
            overlap = max(0.0, min(end, existing_end) - max(start, existing_start))
            shorter = min(duration, max(existing_end - existing_start, 0.001))
            if overlap / shorter >= 0.5:
                duplicate = True
                break
        if not duplicate:
            merged.append(candidate)
    return sorted(merged, key=lambda item: (item.get("start", 0), item.get("end", 0)))


class VideoTranscriber:
    def __init__(self, model_size: str = "base"):
        """
        model_size: "tiny"(75MB), "base"(150MB — супер быстрый!), "small"(500MB)
        """
        self.model_size = model_size
        self._model = None  # Ленивая загрузка — грузим только при первом вызове

    def set_model_size(self, model_size: str) -> None:
        """Меняет модель. Уже загруженная сбрасывается, новая подтянется при вызове."""
        if model_size and model_size != self.model_size:
            self.model_size = model_size
            self._model = None

    def _load_model(self):
        """Загружает модель Whisper в папку на диске X."""
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper не установлен. Запустите: pip install faster-whisper"
            )
        
        models_dir = Path(__file__).parent.parent.resolve() / "models_cache"
        models_dir.mkdir(parents=True, exist_ok=True)

        self._model = WhisperModel(
            self.model_size,
            device="cpu",
            compute_type="int8",
            download_root=str(models_dir),
        )
        return self._model

    def _extract_audio(self, video_path: str, out_path: str) -> str:
        """Извлекает аудио из видео в WAV формат для Whisper."""
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",                    # только аудио
            "-acodec", "pcm_s16le",   # WAV PCM
            "-ar", "16000",           # 16kHz — требование Whisper
            "-ac", "1",               # моно
            out_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise RuntimeError("FFmpeg не найден. Установите FFmpeg и добавьте его в PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Не удалось извлечь аудио: {(exc.stderr or '')[-1200:]}") from exc
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"Не удалось извлечь аудио: {result.stderr[-1200:]}")
        return out_path

    def transcribe(
        self,
        video_path: str,
        language: str | None = None,  # None = автоопределение
        on_progress: Callable[[str, int], None] | None = None,
        control: OperationControl | None = None,
        on_partial: Callable[[dict], None] | None = None,
        on_control_state: Callable[[str], None] | None = None,
        resume_from: float = 0.0,
        retained_result: dict | None = None,
    ) -> dict:
        """
        Основной метод транскрипции.
        
        Возвращает:
        {
            "full_text": str,          # весь текст
            "language": str,           # определённый язык
            "segments": [...],         # сегменты с таймкодами
            "words": [...],            # каждое слово с таймкодом
            "paragraphs": [...],       # логические блоки (для поиска историй)
            "duration": float,
        }
        """
        def _prog(msg, pct):
            if on_progress:
                on_progress(msg, pct)

        _prog("Загружаю модель Whisper...", 5)
        model = self._load_model()

        _prog("Извлекаю аудиодорожку...", 15)
        temp_root = Path(__file__).parent.parent.resolve() / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(dir=str(temp_root))
        audio_path = os.path.join(tmp_dir, "audio.wav")

        try:
            self._extract_audio(video_path, audio_path)

            _prog("Транскрибирую речь... (это займёт несколько минут)", 25)

            transcribe_options = {
                "language": language,
                "word_timestamps": True,
                "vad_filter": True,
                "vad_parameters": {"min_silence_duration_ms": 500},
                "beam_size": 1,
                "best_of": 1,
                "condition_on_previous_text": False,
            }
            if resume_from > 0:
                transcribe_options["clip_timestamps"] = [max(0.0, resume_from - 1.0)]
            segments_gen, info = model.transcribe(audio_path, **transcribe_options)

            _prog(f"Распознаю речь (язык: {info.language})...", 40)

            all_segments = copy.deepcopy((retained_result or {}).get("segments", []))
            last_reported_pct = 40
            is_complete = True
            iterator = iter(segments_gen)

            while True:
                if control is not None:
                    if control.stop_requested:
                        if on_control_state:
                            on_control_state("stopped")
                        is_complete = False
                        break
                    if control.pause_requested:
                        if on_control_state:
                            on_control_state("paused")
                        if not control.wait_if_paused():
                            if on_control_state:
                                on_control_state("stopped")
                            is_complete = False
                            break
                        if on_control_state:
                            on_control_state("running")
                try:
                    seg = next(iterator)
                except StopIteration:
                    break
                seg_data = {
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text.strip(),
                    "words": [],
                }

                if seg.words:
                    for w in seg.words:
                        word_data = {
                            "word": w.word.strip(),
                            "start": round(w.start, 3),
                            "end": round(w.end, 3),
                            "probability": round(w.probability, 3),
                        }
                        seg_data["words"].append(word_data)

                all_segments = merge_transcript_segments(all_segments, [seg_data])

                partial = self._build_result(
                    all_segments,
                    info,
                    retained_result,
                    is_complete=False,
                )
                if on_partial:
                    on_partial(copy.deepcopy(partial))

                # faster-whisper performs most of the work while this generator
                # is consumed. Report progress by the latest processed timestamp
                # so long videos do not look frozen at 40%.
                if info.duration:
                    speech_pct = 40 + int(min(seg.end / info.duration, 1.0) * 43)
                    if speech_pct >= last_reported_pct + 2:
                        processed_min = seg.end / 60
                        total_min = info.duration / 60
                        _prog(
                            f"Whisper распознаёт речь: {processed_min:.1f} из {total_min:.1f} мин...",
                            speech_pct,
                        )
                        last_reported_pct = speech_pct

            if is_complete:
                _prog("Формирую параграфы и блоки...", 85)
                _prog("Транскрипция завершена!", 100)
            else:
                _prog("Транскрипция остановлена, прогресс сохранён", last_reported_pct)

            return self._build_result(
                all_segments,
                info,
                retained_result,
                is_complete=is_complete,
            )

        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_result(
        self,
        segments: list[dict],
        info,
        retained_result: dict | None,
        is_complete: bool,
    ) -> dict:
        all_words = [
            copy.deepcopy(word)
            for segment in segments
            for word in segment.get("words", [])
        ]
        full_text = " ".join(
            segment.get("text", "").strip()
            for segment in segments
            if segment.get("text", "").strip()
        )
        checkpoint = max(
            (float(segment.get("end", 0.0)) for segment in segments),
            default=float((retained_result or {}).get("checkpoint", 0.0)),
        )
        return {
            "full_text": full_text,
            "language": getattr(info, "language", None)
            or (retained_result or {}).get("language"),
            "language_probability": round(
                float(
                    getattr(info, "language_probability", 0.0)
                    or (retained_result or {}).get("language_probability", 0.0)
                ),
                3,
            ),
            "duration": round(
                float(
                    getattr(info, "duration", 0.0)
                    or (retained_result or {}).get("duration", 0.0)
                ),
                2,
            ),
            "segments": copy.deepcopy(segments),
            "words": all_words,
            "paragraphs": self._build_paragraphs(segments, pause_threshold=2.0),
            "checkpoint": round(checkpoint, 3),
            "is_complete": is_complete,
        }

    def _build_paragraphs(
        self,
        segments: list[dict],
        pause_threshold: float = 2.0,
    ) -> list[dict]:
        """
        Группирует сегменты в логические параграфы по паузам в речи.
        Пауза > pause_threshold секунд = граница параграфа.
        
        Параграфы важны для поиска историй — история обычно идёт одним блоком
        без длинных пауз.
        """
        if not segments:
            return []

        paragraphs = []
        current_para = {
            "start": segments[0]["start"],
            "end": segments[0]["end"],
            "text": segments[0]["text"],
            "segment_count": 1,
        }

        for seg in segments[1:]:
            gap = seg["start"] - current_para["end"]
            if gap > pause_threshold:
                # Большая пауза — завершаем текущий параграф
                paragraphs.append(current_para)
                current_para = {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"],
                    "segment_count": 1,
                }
            else:
                # Продолжаем параграф
                current_para["end"] = seg["end"]
                current_para["text"] += " " + seg["text"]
                current_para["segment_count"] += 1

        paragraphs.append(current_para)
        return paragraphs
