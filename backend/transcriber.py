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

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Awaitable


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
        language: str = None,  # None = автоопределение
        on_progress: Callable[[str, int], None] | None = None,
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

            segments_gen, info = model.transcribe(
                audio_path,
                language=language,
                word_timestamps=True,       # таймкод каждого слова
                vad_filter=True,            # фильтр тишины — пропускаем паузы
                vad_parameters={"min_silence_duration_ms": 500},
                beam_size=1,
                best_of=1,
                condition_on_previous_text=False,
            )

            _prog(f"Распознаю речь (язык: {info.language})...", 40)

            # Собираем все сегменты
            all_segments = []
            all_words = []
            full_text_parts = []
            last_reported_pct = 40

            for seg in segments_gen:
                seg_data = {
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text.strip(),
                    "words": [],
                }
                full_text_parts.append(seg.text.strip())

                if seg.words:
                    for w in seg.words:
                        word_data = {
                            "word": w.word.strip(),
                            "start": round(w.start, 3),
                            "end": round(w.end, 3),
                            "probability": round(w.probability, 3),
                        }
                        seg_data["words"].append(word_data)
                        all_words.append(word_data)

                all_segments.append(seg_data)

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

            _prog("Формирую параграфы и блоки...", 85)

            paragraphs = self._build_paragraphs(all_segments, pause_threshold=2.0)
            full_text = " ".join(full_text_parts)

            _prog("Транскрипция завершена!", 100)

            return {
                "full_text": full_text,
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
                "duration": round(info.duration, 2),
                "segments": all_segments,
                "words": all_words,
                "paragraphs": paragraphs,
            }

        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

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
