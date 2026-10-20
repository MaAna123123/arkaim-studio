"""
video_processor.py — работа с FFmpeg и OpenCV:
  - Извлечение метаданных (длительность, fps, разрешение)
  - Генерация превью-кадров для временной шкалы
  - Быстрая нарезка клипов в оригинале (stream copy -c copy)
  - Нарезка в вертикальном формате 9:16 для Shorts/TikTok (fit + black padding)
  - Склейка нескольких фрагментов в один ролик
"""

import json
import os
import subprocess
import shutil
import tempfile
import textwrap
from pathlib import Path

from clip_compositor import build_composition_filter


class VideoProcessor:
    def check_ffmpeg(self) -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    @staticmethod
    def _run(cmd: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Не найден исполняемый файл {cmd[0]}. Установите FFmpeg и добавьте его в PATH.") from exc
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or "неизвестная ошибка").strip()
            raise RuntimeError(f"Ошибка {cmd[0]}: {details[-1200:]}") from exc

    # ─── Metadata ────────────────────────────────────────────────────────────
    def extract_metadata(self, video_path: str) -> dict:
        """Return duration, fps, width, height via ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-show_format",
            video_path,
        ]
        result = self._run(cmd)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("FFprobe вернул некорректные метаданные видео") from exc

        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"), {}
        )
        fmt = data.get("format", {})

        duration = float(fmt.get("duration", 0))
        fps_raw = video_stream.get("r_frame_rate", "30/1")
        try:
            num, den = fps_raw.split("/")
            fps = round(float(num) / float(den), 2)
        except Exception:
            fps = 30.0

        return {
            "duration": duration,
            "fps": fps,
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "size_bytes": int(fmt.get("size", 0)),
            "codec": video_stream.get("codec_name", "unknown"),
        }

    # ─── Thumbnails ───────────────────────────────────────────────────────────
    def extract_thumbnails(
        self, video_path: str, thumbs_dir: str, video_id: str, count: int = 20
    ) -> list[str]:
        """Extract `count` evenly-spaced thumbnail frames."""
        meta = self.extract_metadata(video_path)
        duration = meta["duration"]
        if duration <= 0:
            return []

        urls = []
        for i in range(count):
            t = (duration / count) * i + (duration / count) * 0.5
            out_name = f"{video_id}_thumb_{i:03d}.jpg"
            out_path = os.path.join(thumbs_dir, out_name)
            cmd = [
                "ffmpeg", "-ss", str(t), "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=160:-1",
                "-q:v", "5",
                "-y", out_path,
            ]
            try:
                self._run(cmd)
            except RuntimeError:
                continue
            if os.path.exists(out_path):
                urls.append(f"/thumbs/{out_name}")
        return urls

    # ─── Subtitle ASS Generator ───────────────────────────────────────────────
    def _create_ass_subtitles(
        self, words: list[dict], t_start: float, t_end: float,
        play_res_x: int = 720, play_res_y: int = 1280,
    ) -> str:
        """Generates temporary ASS subtitle file for FFmpeg subtitle filter."""
        clips_dir = Path(__file__).parent.resolve() / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        clip_words = [
            w for w in (words or [])
            if float(w.get("end", 0)) >= t_start and float(w.get("start", 0)) <= t_end
        ]

        phrases = []
        curr = []
        for i, w in enumerate(clip_words):
            curr.append(w)
            word_str = w.get("word", "").strip()
            next_w = clip_words[i + 1] if i + 1 < len(clip_words) else None
            pause_to_next = (float(next_w.get("start", 0)) - float(w.get("end", 0))) if next_w else 0.0

            # Split into compact 3-word phrases, or on sentence punctuation (. ! ? ; :), or pause > 0.5s
            if len(curr) >= 3 or word_str.endswith((".", "!", "?", ";", ":")) or pause_to_next > 0.5:
                phrases.append(curr)
                curr = []
        if curr:
            phrases.append(curr)

        def fmt_ass(s_val: float) -> str:
            s = max(0.0, s_val)
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            secs = int(s % 60)
            cs = int(round((s - int(s)) * 100))
            if cs >= 100:
                cs = 99
            return f"{h}:{m:02d}:{secs:02d}.{cs:02d}"

        events = []
        for p in phrases:
            if not p:
                continue
            p_start = float(p[0].get("start", 0)) - t_start
            p_end = float(p[-1].get("end", 0)) - t_start
            p_text = " ".join([w.get("word", "").strip() for w in p])
            if p_text and p_end > p_start:
                # Add smooth fade-in and fade-out (\fad(120,120))
                events.append(f"Dialogue: 0,{fmt_ass(p_start)},{fmt_ass(p_end)},Default,,0,0,0,,{{\\fad(120,120)}}{p_text}")

        resolution_scale = max(1.0, float(play_res_y) / 1280.0)
        font_size = int(round(38 * resolution_scale))
        outline = round(3.5 * resolution_scale, 1)
        shadow = round(2 * resolution_scale, 1)
        horizontal_margin = int(round(30 * resolution_scale))
        vertical_margin = int(round(120 * resolution_scale))
        ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{horizontal_margin},{horizontal_margin},{vertical_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"

        ass_handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".ass", delete=False, dir=str(clips_dir)
        )
        ass_handle.write(ass_header)
        ass_handle.close()
        return ass_handle.name

    def _create_text_ad_filters(
        self,
        text: str,
        position: str = "top",
        interval: float = 4.0,
        opacity: float = 0.75,
        w: int = 720,
        h: int = 1280,
    ) -> tuple[list[str], list[str]]:
        """
        Создаёт FFmpeg drawtext-фильтры для сменяющихся полупрозрачных рекламных текстовых плашек.
        Поддерживает мультистиховой авто-смену по таймеру и экранирование спецсимволов.
        """
        if not text or not text.strip():
            return [], []

        phrases = [p.strip() for p in text.strip().splitlines() if p.strip()]
        if not phrases:
            return [], []

        temp_files = []
        temp_dir = Path(__file__).parent.parent.resolve() / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        if position == "bottom":
            y_expr = "h-text_h-110"
        elif position == "center":
            y_expr = "(h-text_h)/2"
        else:  # top (default)
            y_expr = "75"

        font_size = 32 if w < h else 36
        box_alpha = max(0.1, min(0.9, opacity * 0.65))
        font_alpha = max(0.1, min(1.0, opacity))

        filters = []
        n = len(phrases)
        total_cycle = max(1.0, n * interval)

        for i, phrase in enumerate(phrases):
            phrase_escaped = phrase.replace("\\", "\\\\").replace("%", "\\%").replace(":", "\\:")
            handle = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".txt", delete=False, dir=str(temp_dir)
            )
            handle.write(phrase_escaped)
            handle.close()
            temp_files.append(handle.name)

            escaped_file = Path(handle.name).as_posix().replace(":", "\\:").replace("'", "\\'")

            filter_str = (
                f"drawtext="
                f"fontfile='C\\:/Windows/Fonts/arial.ttf':"
                f"textfile='{escaped_file}':reload=0:"
                f"fontcolor=white@{font_alpha:.2f}:fontsize={font_size}:"
                f"borderw=2:bordercolor=black@{font_alpha:.2f}:"
                f"box=1:boxcolor=black@{box_alpha:.2f}:boxborderw=12:"
                f"x=(w-text_w)/2:y={y_expr}"
            )
            if n > 1:
                start_t = i * interval
                end_t = (i + 1) * interval
                filter_str += f":enable='between(mod(t,{total_cycle:.2f}),{start_t:.2f},{end_t:.2f})'"

            filters.append(filter_str)

        return filters, temp_files

    def _get_banner_crop(self, banner_path: str) -> tuple[str | None, int, int]:
        """
        Обнаруживает область активного контента (без чёрных рамок) в файле баннера.
        Возвращает (crop_str, cropped_w, cropped_h).
        """
        try:
            import re
            cmd = [
                "ffmpeg", "-i", banner_path,
                "-vframes", "10",
                "-vf", "cropdetect=limit=30:round=2",
                "-f", "null", "-"
            ]
            res = self._run(cmd)
            matches = re.findall(r"crop=(\d+:\d+:\d+:\d+)", res.stderr)
            if matches:
                crop_str = matches[-1]
                parts = crop_str.split(":")
                cw, ch = int(parts[0]), int(parts[1])
                return crop_str, cw, ch
        except Exception:
            pass
        return None, 0, 0

    # ─── Mini Video Banner (looping overlay at top) ───────────────────────────
    def insert_video_banner(
        self,
        clip_path: str,
        banner_path: str,
        out_path: str,
        banner_height: int = 340,
        banner_y: int = None,
    ) -> str:
        """
        Накладывает зацикленное мини-видео (баннер) поверх клипа в верхней части кадра.
        Автоматически обрезает чёрные поля самого баннера и масштабирует чистое изображение
        под заданную высоту (по умолчанию 340px).
        """
        if not os.path.exists(clip_path):
            raise FileNotFoundError(f"Клип не найден: {clip_path}")
        if not os.path.exists(banner_path):
            raise FileNotFoundError(f"Баннер-видео не найден: {banner_path}")

        meta = self.extract_metadata(clip_path)
        clip_w = meta["width"] or 720
        clip_h = meta["height"] or 1280
        clip_fps = meta["fps"] or 24.0
        clip_dur = meta["duration"]

        # Детектируем и обрезаем чёрные рамки файла баннера
        crop_str, b_w, b_h = self._get_banner_crop(banner_path)

        if not b_w or not b_h:
            banner_meta = self.extract_metadata(banner_path)
            b_w = banner_meta.get("width") or 720
            b_h = banner_meta.get("height") or 400

        b_aspect = b_w / max(1, b_h)

        # Растягиваем баннер на всю ширину клипа (или 98%), чтобы текст на нём был крупным и читаемым
        target_w = int(clip_w * 0.98) if clip_h > clip_w else int(clip_w * 0.6)
        target_h = int(target_w / b_aspect)

        # Если задана ограниченная высота баннера, корректируем
        if banner_height and banner_height > 0:
            if target_h > banner_height:
                target_h = banner_height
                target_w = int(target_h * b_aspect)
                if target_w > clip_w:
                    target_w = clip_w
                    target_h = int(target_w / b_aspect)

        # Выравниваем размеры до чётных чисел для H.264
        target_w = max(2, target_w - (target_w % 2))
        target_h = max(2, target_h - (target_h % 2))

        # Размещаем в верхней части кадра
        top_black_h = (clip_h - (clip_w * 3 / 4)) / 2 if clip_h > clip_w else 0
        if banner_y is None:
            if top_black_h > target_h:
                banner_y = max(5, int((top_black_h - target_h) / 2))
            else:
                banner_y = 10

        crop_filter = f"crop={crop_str}," if crop_str else ""
        filter_complex = (
            f"[0:v]setpts=PTS-STARTPTS,fps={clip_fps},setsar=1[vmain];"
            f"[1:v]{crop_filter}setpts=PTS-STARTPTS,scale={target_w}:{target_h},fps={clip_fps},setsar=1[vbanner];"
            f"[vmain][vbanner]overlay=x=(main_w-overlay_w)/2:y={banner_y}:shortest=1[outv]"
        )

        cmd = [
            "ffmpeg",
            "-i", clip_path,
            "-stream_loop", "-1", "-i", banner_path,
            "-t", str(clip_dur),
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            "-y", out_path,
        ]

        self._run(cmd)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("FFmpeg не создал клип с видео-баннером")
        return out_path

    # ─── Clip Video (Original or 9:16 Shorts) ─────────────────────────────────
    def clip_video(
        self,
        video_path: str,
        start: float,
        end: float,
        out_path: str,
        padding: float = 0.5,
        aspect_ratio: str = "original",  # "original" | "9:16"
        title: str = "",
        burn_subtitles: bool = True,
        words: list[dict] = None,
        text_ad_enabled: bool = False,
        text_ad_text: str = "",
        text_ad_position: str = "top",
        text_ad_interval: float = 4.0,
        text_ad_opacity: float = 0.75,
        video_banner_enabled: bool = False,
        video_banner_path: str = "",
        video_banner_height: int = 380,
        composition: dict | None = None,
        overlay_ad_path: str = "",
    ) -> str:
        """
        Cut a single segment.
        - "original": instant stream copy (-c copy)
        - "9:16": fit the whole frame into 9:16 with black padding and optional ASS subtitles
        - Supports semi-transparent dynamic text ad overlay
        """
        meta = self.extract_metadata(video_path)
        duration = meta["duration"]

        if composition:
            t_start = float(composition["source_start"])
            t_end = float(composition["source_end"])
        else:
            t_start = max(0.0, start - padding)
            t_end = min(duration, end + padding)
        seg_dur = t_end - t_start

        if seg_dur <= 0:
            raise ValueError("Фрагмент находится за пределами видео")

        temp_title_file = None
        temp_ass_file = None
        temp_text_ad_files = []
        title_filter = None
        if title.strip():
            temp_root = Path(__file__).parent.parent.resolve() / "temp"
            temp_root.mkdir(parents=True, exist_ok=True)
            wrapped_title = "\n".join(textwrap.wrap(title.strip(), width=34))
            title_handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".txt",
                delete=False,
                dir=str(temp_root),
            )
            title_handle.write(wrapped_title)
            title_handle.close()
            temp_title_file = title_handle.name
            escaped_path = Path(temp_title_file).as_posix().replace(":", "\\:").replace("'", "\\'")
            title_filter = (
                "drawtext="
                "fontfile='C\\:/Windows/Fonts/arial.ttf':"
                f"textfile='{escaped_path}':reload=0:"
                "fontcolor=white:fontsize=34:line_spacing=8:"
                "borderw=3:bordercolor=black@0.95:"
                "box=1:boxcolor=black@0.55:boxborderw=16:"
                "x=(w-text_w)/2:y=55"
            )

        composition_vertical = bool(composition and composition.get("output_aspect_ratio") == "9:16")
        output_width = 1080 if composition_vertical else (720 if aspect_ratio == "9:16" else int(meta.get("width") or 1280))
        output_height = 1920 if composition_vertical else (1280 if aspect_ratio == "9:16" else int(meta.get("height") or 720))

        if burn_subtitles and words:
            try:
                temp_ass_file = self._create_ass_subtitles(
                    words, t_start, t_end, play_res_x=output_width, play_res_y=output_height
                )
            except Exception:
                temp_ass_file = None

        text_ad_filters = []
        if text_ad_enabled and text_ad_text.strip():
            text_ad_filters, temp_text_ad_files = self._create_text_ad_filters(
                text_ad_text, text_ad_position, text_ad_interval, text_ad_opacity,
                output_width, output_height,
            )

        needs_encode = (
            aspect_ratio == "9:16" or temp_ass_file or title_filter or text_ad_filters or composition
        )

        filters = ["setpts=PTS-STARTPTS"]
        if aspect_ratio == "9:16":
            filters.extend([
                "scale=720:1280:force_original_aspect_ratio=decrease",
                "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black",
                "setsar=1",
            ])
        else:
            # Force encode path but keep original resolution
            filters.append("setsar=1")
        if title_filter:
            filters.append(title_filter)
        if temp_ass_file and os.path.exists(temp_ass_file):
            escaped_ass = Path(temp_ass_file).as_posix().replace(":", "\\:").replace("'", "\\'")
            filters.append(f"subtitles='{escaped_ass}'")
        if text_ad_filters:
            filters.extend(text_ad_filters)

        video_filter_args = ["-vf", ",".join(filters)]
        video_map_args = []
        overlay_input_args = []
        if composition:
            source_w = int(meta.get("width") or 0)
            source_h = int(meta.get("height") or 0)
            if source_w <= 0 or source_h <= 0:
                raise ValueError("Не удалось определить размер исходного видео")

            next_input = 1
            banner_input = None
            banner_layer = composition.get("banner_layer")
            if banner_layer and banner_layer.get("enabled", True):
                if not video_banner_path or not os.path.exists(video_banner_path):
                    raise FileNotFoundError("Видео-баннер для композиции не найден")
                banner_input = next_input
                next_input += 1
                overlay_input_args.extend(["-stream_loop", "-1", "-i", video_banner_path])

            ad_input = None
            ad_layer = composition.get("ad_layer")
            if ad_layer and ad_layer.get("enabled", True) and ad_layer.get("mode") == "overlay":
                if not overlay_ad_path or not os.path.exists(overlay_ad_path):
                    raise FileNotFoundError("Видео-реклама для композиции не найдена")
                ad_input = next_input
                overlay_input_args.extend(["-stream_loop", "-1", "-i", overlay_ad_path])

            post_filters = []
            if title_filter:
                post_filters.append(title_filter)
            if temp_ass_file and os.path.exists(temp_ass_file):
                escaped_ass = Path(temp_ass_file).as_posix().replace(":", "\\:").replace("'", "\\'")
                post_filters.append(f"subtitles='{escaped_ass}'")
            post_filters.extend(text_ad_filters)
            composition_filter = build_composition_filter(
                composition, source_w, source_h, output_width, output_height,
                banner_input, ad_input, post_filters,
            )
            video_filter_args = ["-filter_complex", composition_filter.filter_complex]
            video_map_args = ["-map", composition_filter.video_map, "-map", "0:a?"]

        clip_cmd = [
            "ffmpeg",
            "-ss", str(t_start),
            "-i", video_path,
            *overlay_input_args,
            "-t", str(seg_dur),
            *video_filter_args,
            *video_map_args,
            "-af", "asetpts=PTS-STARTPTS",
            "-c:v", "libx264",
            "-preset", "medium" if composition else "ultrafast",
            "-crf", "18" if composition else "22",
            "-pix_fmt", "yuv420p",
            "-threads", "0",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            "-y", out_path,
        ]

        try:
            self._run(clip_cmd)
        finally:
            if temp_title_file:
                Path(temp_title_file).unlink(missing_ok=True)
            if temp_ass_file:
                Path(temp_ass_file).unlink(missing_ok=True)
            for tf in temp_text_ad_files:
                Path(tf).unlink(missing_ok=True)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("FFmpeg не создал выходной клип")

        # ── Video banner (looping mini-video overlay at top) ─────────────────
        if not composition and video_banner_enabled and video_banner_path and os.path.exists(video_banner_path):
            banner_tmp = out_path + "_banner_tmp.mp4"
            try:
                self.insert_video_banner(
                    out_path, video_banner_path, banner_tmp, banner_height=video_banner_height
                )
                os.replace(banner_tmp, out_path)
            except Exception as banner_err:
                # Non-fatal: log and keep clip without banner
                Path(banner_tmp).unlink(missing_ok=True)
                import logging
                logging.getLogger(__name__).warning(f"Видео-баннер не применён: {banner_err}")

        return out_path

    def _has_audio(self, video_path: str) -> bool:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_streams", "-select_streams", "a",
            "-print_format", "json", video_path
        ]
        try:
            res = self._run(cmd)
            data = json.loads(res.stdout)
            return len(data.get("streams", [])) > 0
        except Exception:
            return False

    # ─── Ad Insertion ─────────────────────────────────────────────────────────
    def insert_ad_into_clip(
        self,
        clip_path: str,
        ad_path: str,
        out_path: str,
    ) -> str:
        """
        Вставляет рекламный ролик ровно в середину клипа за 1 проход через filter_complex.
        Генерирует чистые непрерывные PTS-таймкоды для 100% плавного воспроизведения
        без подвисаний и правильно заполняет кадр рекламой без гигантских черных рамок.
        Result: first_half + ad + second_half
        """
        if not os.path.exists(clip_path):
            raise FileNotFoundError(f"Клип не найден: {clip_path}")
        if not os.path.exists(ad_path):
            raise FileNotFoundError(f"Рекламный ролик не найден: {ad_path}")

        clip_meta = self.extract_metadata(clip_path)
        clip_dur = clip_meta["duration"]
        clip_w = clip_meta["width"]
        clip_h = clip_meta["height"]
        clip_fps = clip_meta["fps"] or 24.0

        if clip_dur <= 0:
            raise ValueError("Клип пуст или повреждён")

        mid_point = round(clip_dur / 2.0, 3)

        # Выбираем режим масштабирования: для вертикальных Shorts (9:16) заполняем весь кадр
        if clip_w < clip_h:  # Shorts 9:16
            ad_scale = f"scale={clip_w}:{clip_h}:force_original_aspect_ratio=increase,crop={clip_w}:{clip_h},fps={clip_fps},setsar=1"
        else:  # Original / Landscape 16:9
            ad_scale = f"scale={clip_w}:{clip_h}:force_original_aspect_ratio=decrease,pad={clip_w}:{clip_h}:(ow-iw)/2:(oh-ih)/2:black,fps={clip_fps},setsar=1"

        ad_has_audio = self._has_audio(ad_path)
        if ad_has_audio:
            ad_audio_filter = f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS[a2];"
        else:
            ad_meta = self.extract_metadata(ad_path)
            ad_dur = ad_meta.get("duration") or 5.0
            ad_audio_filter = f"anullsrc=channel_layout=stereo:sample_rate=44100:duration={ad_dur}[a2];"

        filter_complex = (
            f"[0:v]trim=0:{mid_point},setpts=PTS-STARTPTS,fps={clip_fps},setsar=1,format=yuv420p[v1];"
            f"[0:a]atrim=0:{mid_point},asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a1];"
            f"[1:v]{ad_scale},setpts=PTS-STARTPTS,format=yuv420p[v2];"
            f"{ad_audio_filter}"
            f"[0:v]trim={mid_point}:{clip_dur},setpts=PTS-STARTPTS,fps={clip_fps},setsar=1,format=yuv420p[v3];"
            f"[0:a]atrim={mid_point}:{clip_dur},asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a3];"
            f"[v1][a1][v2][a2][v3][a3]concat=n=3:v=1:a=1[outv][outa]"
        )

        cmd = [
            "ffmpeg", "-i", clip_path, "-i", ad_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            "-y", out_path,
        ]

        self._run(cmd)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("FFmpeg не создал клип с рекламной вставкой")

        return out_path

    # ─── Concatenate Clips ────────────────────────────────────────────────────
    def export_concatenated(
        self,
        video_path: str,
        segments: list[dict],
        out_path: str,
        padding: float = 0.5,
        aspect_ratio: str = "original",
        burn_subtitles: bool = True,
        words: list[dict] = None,
        text_ad_enabled: bool = False,
        text_ad_text: str = "",
        text_ad_position: str = "top",
        text_ad_interval: float = 4.0,
        text_ad_opacity: float = 0.75,
        video_banner_enabled: bool = False,
        video_banner_path: str = "",
        video_banner_height: int = 380,
    ) -> str:
        """Clip each segment to a temp file, then concatenate all into one final video."""
        if not segments:
            raise ValueError("Не выбраны фрагменты для склейки")

        temp_root = Path(__file__).parent.parent.resolve() / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(dir=str(temp_root))
        concat_list = os.path.join(tmp_dir, "concat.txt")
        clip_paths = []

        try:
            for i, seg in enumerate(segments):
                clip_path = os.path.join(tmp_dir, f"clip_{i:04d}.mp4")
                self.clip_video(
                    video_path,
                    seg["start"],
                    seg["end"],
                    clip_path,
                    padding=padding,
                    aspect_ratio=aspect_ratio,
                    title="",
                    burn_subtitles=burn_subtitles,
                    words=words,
                    text_ad_enabled=text_ad_enabled,
                    text_ad_text=text_ad_text,
                    text_ad_position=text_ad_position,
                    text_ad_interval=text_ad_interval,
                    text_ad_opacity=text_ad_opacity,
                    video_banner_enabled=video_banner_enabled,
                    video_banner_path=video_banner_path,
                    video_banner_height=video_banner_height,
                )
                clip_paths.append(clip_path)

            with open(concat_list, "w", encoding="utf-8") as f:
                for cp in clip_paths:
                    escaped_path = Path(cp).as_posix().replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")

            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list,
                "-c", "copy",
                "-y", out_path,
            ]
            self._run(cmd)
            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                raise RuntimeError("FFmpeg не создал итоговый ролик")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return out_path
