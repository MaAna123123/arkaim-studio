"""
youtube_downloader.py — скачивание видео и субтитров с YouTube / VK / TikTok через yt-dlp.

Ключевые функции:
- get_info(url)              : метаданные + выбранная дорожка субтитров (без гигантского payload)
- get_youtube_transcript()   : готовый транскрипт с ПОСЛОВНЫМИ таймкодами прямо из YouTube
- download(url, ...)         : скачать видеофайл
- extract_chapters(info)     : главы YouTube → сегменты
- get_instant_clips(...)     : мгновенные нарезки по смысловым границам

ВАЖНО про формат json3:
YouTube отдаёт субтитры в формате json3, где у каждого слова есть собственный
tOffsetMs. Это даёт настоящие пословные таймкоды — их же использует Whisper.
Поэтому при наличии субтитров запускать Whisper не нужно вообще.
"""

import json
import re
import urllib.request
from pathlib import Path
from typing import Callable, Optional

# Приоритет языков при выборе дорожки субтитров
_LANG_PRIORITY = ["ru", "ru-RU", "en", "en-US", "en-GB"]

# Форматы субтитров по убыванию удобства парсинга
_FORMAT_PRIORITY = ["json3", "srv3", "srv2", "srv1", "vtt", "ttml"]


def _get_ydl_opts(quiet: bool = True) -> dict:
    return {
        "quiet": quiet,
        "no_warnings": quiet,
        "extract_flat": False,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "socket_timeout": 20,
        "retries": 3,
        "extractor_args": {
            "youtube": {
                # web_safari/mweb отдают субтитры стабильнее, чем android-клиент
                "player_client": ["web_safari", "mweb", "android", "ios", "web"],
            }
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Выбор дорожки субтитров
# ─────────────────────────────────────────────────────────────────────────────

def _pick_subtitle_track(raw_info: dict) -> Optional[dict]:
    """
    Выбирает лучшую дорожку субтитров из сырого ответа yt-dlp.

    Возвращает компактный словарь {url, ext, lang, is_auto} либо None.
    Возвращать целиком raw_info["subtitles"] нельзя — там сотни языков
    с огромными URL, это раздувает JSON-ответ на мегабайты.
    """
    manual = raw_info.get("subtitles") or {}
    automatic = raw_info.get("automatic_captions") or {}

    def _tracks_for(lang_code: str) -> tuple[Optional[list], bool]:
        if manual.get(lang_code):
            return manual[lang_code], False
        if automatic.get(lang_code):
            return automatic[lang_code], True
        return None, False

    tracks, is_auto = None, False
    chosen_lang = ""

    # 1. Приоритетные языки: сначала ручные, потом автоматические
    for lang in _LANG_PRIORITY:
        tracks, is_auto = _tracks_for(lang)
        if tracks:
            chosen_lang = lang
            break

    # 2. Языки с префиксом (ru-orig, en-US и т.п.)
    if not tracks:
        for source, auto_flag in ((manual, False), (automatic, True)):
            for lang_code, lang_tracks in source.items():
                base = lang_code.split("-")[0].lower()
                if base in ("ru", "en") and lang_tracks:
                    tracks, is_auto, chosen_lang = lang_tracks, auto_flag, lang_code
                    break
            if tracks:
                break

    # 3. Хоть что-нибудь
    if not tracks:
        for source, auto_flag in ((manual, False), (automatic, True)):
            for lang_code, lang_tracks in source.items():
                if lang_tracks:
                    tracks, is_auto, chosen_lang = lang_tracks, auto_flag, lang_code
                    break
            if tracks:
                break

    if not tracks:
        return None

    # Выбираем самый удобный формат
    chosen = None
    for fmt in _FORMAT_PRIORITY:
        chosen = next((t for t in tracks if t.get("ext") == fmt), None)
        if chosen:
            break
    if not chosen:
        chosen = tracks[0]

    if not chosen.get("url"):
        return None

    return {
        "url": chosen["url"],
        "ext": chosen.get("ext", "json3"),
        "lang": chosen_lang or chosen.get("name", ""),
        "is_auto": is_auto,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Метаданные
# ─────────────────────────────────────────────────────────────────────────────

def get_info(url: str) -> dict:
    """
    Возвращает метаданные видео без скачивания.

    В отличие от прежней версии, СОХРАНЯЕТ выбранную дорожку субтитров в ключе
    `subtitle_track` — без него get_youtube_transcript() не мог найти субтитры
    и всегда возвращал has_transcript=False.
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp не установлен. Запустите: pip install yt-dlp")

    opts = {**_get_ydl_opts(), "skip_download": True, "writesubtitles": True}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw_info = ydl.extract_info(url, download=False)
    except Exception as e:
        err_msg = str(e)
        blocked_markers = [
            "Connection aborted", "10054", "UNEXPECTED_EOF", "SSL",
            "Unable to download", "timed out", "Remote end closed",
        ]
        if any(keyword in err_msg for keyword in blocked_markers):
            raise RuntimeError(
                "Доступ к серверам YouTube заблокирован или оборван вашим интернет-провайдером "
                "(ошибка SSL/DPI). Переключитесь на вкладку «Видео с ПК» и загрузите файл с компьютера."
            )
        if "Private video" in err_msg or "members-only" in err_msg:
            raise RuntimeError("Это приватное видео или видео только для участников канала.")
        if "Video unavailable" in err_msg:
            raise RuntimeError("Видео недоступно — возможно, удалено или заблокировано в вашем регионе.")
        raise RuntimeError(f"Не удалось получить инфо о видео: {err_msg}")

    if not raw_info:
        raise RuntimeError("yt-dlp не вернул данные о видео")

    # Плейлист — берём первое видео
    if raw_info.get("_type") == "playlist" and raw_info.get("entries"):
        raw_info = raw_info["entries"][0]

    chapters = extract_chapters(raw_info)
    subtitle_track = _pick_subtitle_track(raw_info)

    return {
        "title": raw_info.get("title", "Без названия"),
        "duration": raw_info.get("duration", 0) or 0,
        "thumbnail": raw_info.get("thumbnail", ""),
        "uploader": raw_info.get("uploader", ""),
        "view_count": raw_info.get("view_count", 0),
        "description": (raw_info.get("description", "") or "")[:1000],
        "chapters": chapters,
        "has_chapters": len(chapters) > 0,
        "platform": _detect_platform(url),
        "url": url,
        # Компактная ссылка на субтитры — то, чего не хватало раньше
        "subtitle_track": subtitle_track,
        "has_subtitles": subtitle_track is not None,
        "subtitle_lang": (subtitle_track or {}).get("lang", ""),
        "subtitle_is_auto": (subtitle_track or {}).get("is_auto", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Скачивание видео
# ─────────────────────────────────────────────────────────────────────────────

def download(
    url: str,
    output_dir: str,
    quality: str = "720",
    filename_stem: Optional[str] = None,
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> str:
    """
    Скачивает видео в output_dir.

    filename_stem: если задан, файл сразу сохраняется под этим именем.
    Это избавляет от переименования после скачивания (раньше os.rename мог
    упасть или подхватить чужой файл при совпадении id).
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp не установлен. Запустите: pip install yt-dlp")

    if quality == "1080":
        fmt = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    elif quality == "best":
        fmt = "bestvideo+bestaudio/best"
    elif quality == "480":
        fmt = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    else:
        fmt = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"

    stem = filename_stem or "%(id)s"
    out_template = str(Path(output_dir) / f"{stem}.%(ext)s")

    downloaded_paths: list[str] = []

    def progress_hook(d):
        status = d.get("status")
        if status == "finished":
            downloaded_paths.append(d.get("filename", ""))
        elif status == "downloading" and progress_cb:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            if total > 0:
                pct = int(done / total * 100)
                mb_done = done / 1024 / 1024
                mb_total = total / 1024 / 1024
                progress_cb(f"Скачиваю видео: {mb_done:.1f} / {mb_total:.1f} МБ", pct)

    opts = {
        **_get_ydl_opts(quiet=True),
        "format": fmt,
        "outtmpl": out_template,
        "progress_hooks": [progress_hook],
        "merge_output_format": "mp4",
        "overwrites": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info and info.get("_type") == "playlist" and info.get("entries"):
            info = info["entries"][0]
        video_id = (info or {}).get("id", "video")

    out_dir = Path(output_dir)

    # 1. Точное имя, которое мы задали
    if filename_stem:
        for candidate in sorted(out_dir.glob(f"{filename_stem}.*")):
            if candidate.is_file() and candidate.suffix.lower() != ".part":
                return str(candidate)

    # 2. Путь из progress_hook (может указывать на промежуточный файл до merge)
    for raw_path in reversed(downloaded_paths):
        if not raw_path:
            continue
        candidate = Path(raw_path)
        if candidate.exists():
            return str(candidate)
        merged = candidate.with_suffix(".mp4")
        if merged.exists():
            return str(merged)

    # 3. Поиск по id видео
    for candidate in sorted(out_dir.glob(f"{video_id}*")):
        if candidate.is_file() and candidate.suffix.lower() not in (".part", ".ytdl"):
            return str(candidate)

    raise FileNotFoundError(f"Не удалось найти скачанный файл для {video_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Главы
# ─────────────────────────────────────────────────────────────────────────────

def extract_chapters(info: dict) -> list[dict]:
    """
    Извлекает главы видео и превращает их в сегменты для нарезки.

    Принимает КАК сырой ответ yt-dlp (ключ start_time), ТАК И уже обработанный
    список (ключи start/end). Раньше функция понимала только сырой формат,
    поэтому при повторном вызове все главы получали start=0.
    """
    chapters = info.get("chapters") or []
    duration = float(info.get("duration") or 0)
    result = []

    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            continue

        # Сырой формат yt-dlp
        if "start_time" in ch:
            start = float(ch.get("start_time") or 0)
            if ch.get("end_time"):
                end = float(ch["end_time"])
            elif i + 1 < len(chapters):
                end = float(chapters[i + 1].get("start_time") or start + 60)
            else:
                end = duration or (start + 60)
        # Уже обработанный формат
        elif "start" in ch:
            start = float(ch.get("start") or 0)
            end = float(ch.get("end") or 0)
            if end <= start:
                end = duration if duration > start else start + 60
        else:
            continue

        if end <= start:
            continue

        title = ch.get("title") or ch.get("label") or f"Глава {i + 1}"

        result.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "label": str(title)[:90],
            "description": f"Глава видео: «{title}» ({_fmt(start)} — {_fmt(end)})",
            "score": 0.85,
            "source": "youtube_chapter",
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Субтитры → транскрипт
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_subtitle_bytes(track_url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(
        track_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_json3(payload: bytes) -> tuple[list[dict], list[dict]]:
    """
    Парсит json3 от YouTube в (segments, words).

    Формат json3:
      events[].tStartMs           — начало реплики
      events[].dDurationMs        — длительность
      events[].segs[].utf8        — текст слова
      events[].segs[].tOffsetMs   — смещение слова внутри реплики  ← пословный тайминг!

    Автосубтитры содержат «катящиеся» дубли (aAppend=1) — их надо отбрасывать,
    иначе текст утраивается и таймкоды съезжают.
    """
    data = json.loads(payload.decode("utf-8", errors="replace"))
    events = data.get("events") or []

    segments: list[dict] = []
    flat_words: list[dict] = []

    for ev in events:
        # Катящийся дубль автосубтитров
        if ev.get("aAppend"):
            continue

        segs = ev.get("segs") or []
        if not segs:
            continue

        start_ms = ev.get("tStartMs")
        if start_ms is None:
            continue
        dur_ms = ev.get("dDurationMs") or 0

        seg_start = start_ms / 1000.0
        seg_end = (start_ms + dur_ms) / 1000.0

        words_list: list[dict] = []
        text_parts: list[str] = []

        for idx, s in enumerate(segs):
            token = s.get("utf8", "")
            if not token.strip():
                continue

            offset_ms = s.get("tOffsetMs", 0) or 0
            w_start = (start_ms + offset_ms) / 1000.0

            # Конец слова — начало следующего слова, иначе конец реплики
            w_end = None
            for nxt in segs[idx + 1:]:
                if nxt.get("utf8", "").strip():
                    w_end = (start_ms + (nxt.get("tOffsetMs", 0) or 0)) / 1000.0
                    break
            if w_end is None or w_end <= w_start:
                w_end = max(seg_end, w_start + 0.25)

            clean = token.strip()
            text_parts.append(clean)
            word_obj = {
                "word": clean,
                "start": round(w_start, 3),
                "end": round(w_end, 3),
                "probability": 1.0,
            }
            words_list.append(word_obj)

        full_text = " ".join(text_parts).strip()
        full_text = re.sub(r"\s+", " ", full_text)
        if not full_text:
            continue

        if words_list:
            seg_start = min(seg_start, words_list[0]["start"])
            seg_end = max(seg_end, words_list[-1]["end"])
        if seg_end <= seg_start:
            seg_end = seg_start + 1.0

        segments.append({
            "start": round(seg_start, 3),
            "end": round(seg_end, 3),
            "text": full_text,
            "words": words_list,
        })
        flat_words.extend(words_list)

    return segments, flat_words


def _parse_vtt_or_xml(payload: bytes, ext: str) -> tuple[list[dict], list[dict]]:
    """Запасной парсер для vtt / srv / ttml, когда json3 недоступен."""
    text = payload.decode("utf-8", errors="replace")

    # XML-подобные форматы (srv1/srv2/srv3/ttml)
    if ext in ("srv1", "srv2", "srv3", "ttml") or text.lstrip().startswith("<"):
        segments: list[dict] = []
        flat_words: list[dict] = []
        pattern = re.compile(
            r'<(?P<tag>text|p)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>',
            re.DOTALL,
        )
        time_attr_pattern = re.compile(r'\b(start|begin|dur|end)="([\d.:]+)"')
        for match in pattern.finditer(text):
            time_attrs = dict(time_attr_pattern.findall(match.group("attrs")))
            start_raw = time_attrs.get("start") or time_attrs.get("begin")
            if not start_raw:
                continue
            try:
                start = _parse_flexible_time(start_raw)
                if "dur" in time_attrs:
                    end = start + _parse_flexible_time(time_attrs["dur"])
                elif "end" in time_attrs:
                    end = _parse_flexible_time(time_attrs["end"])
                else:
                    continue
            except ValueError:
                continue
            body = re.sub(r"<[^>]+>", "", match.group("body"))
            body = _unescape(body).strip()
            body = re.sub(r"\s+", " ", body)
            if not body or end <= start:
                continue
            words = _spread_words(body, start, end)
            segments.append({"start": round(start, 3), "end": round(end, 3), "text": body, "words": words})
            flat_words.extend(words)
        return segments, flat_words

    # WebVTT
    segments, flat_words = [], []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    time_re = re.compile(r"([\d:.]+)\s*-->\s*([\d:.]+)")
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        match_idx = next((i for i, ln in enumerate(lines) if time_re.search(ln)), None)
        if match_idx is None:
            continue
        match = time_re.search(lines[match_idx])
        try:
            start = _parse_flexible_time(match.group(1))
            end = _parse_flexible_time(match.group(2))
        except ValueError:
            continue
        body = " ".join(lines[match_idx + 1:])
        body = re.sub(r"<[^>]+>", "", body)
        body = _unescape(body).strip()
        body = re.sub(r"\s+", " ", body)
        if not body or end <= start:
            continue
        if segments and segments[-1]["text"] == body:
            continue
        words = _spread_words(body, start, end)
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": body, "words": words})
        flat_words.extend(words)
    return segments, flat_words


def _unescape(value: str) -> str:
    import html
    return html.unescape(value)


def _parse_flexible_time(value: str) -> float:
    value = value.strip().replace(",", ".")
    if ":" not in value:
        return float(value)
    parts = value.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def _spread_words(text: str, start: float, end: float) -> list[dict]:
    """Равномерно распределяет слова внутри реплики (для форматов без пословных таймкодов)."""
    tokens = [t for t in re.findall(r"\S+", text) if t]
    if not tokens:
        return []
    duration = max(0.05, end - start)
    weights = [max(1, len(re.sub(r"\W", "", t, flags=re.UNICODE))) for t in tokens]
    total = sum(weights) or 1
    cursor = 0
    words = []
    for token, weight in zip(tokens, weights):
        w_start = start + duration * cursor / total
        cursor += weight
        w_end = start + duration * cursor / total
        words.append({
            "word": token,
            "start": round(w_start, 3),
            "end": round(w_end, 3),
            "probability": 1.0,
            "estimated": True,
        })
    return words


def build_paragraphs(segments: list[dict], pause_threshold: float = 2.0) -> list[dict]:
    """Группирует реплики в смысловые абзацы по паузам."""
    if not segments:
        return []

    paragraphs = []
    current = {
        "start": segments[0]["start"],
        "end": segments[0]["end"],
        "text": segments[0]["text"],
        "segment_count": 1,
    }
    for seg in segments[1:]:
        gap = seg["start"] - current["end"]
        if gap > pause_threshold:
            paragraphs.append(current)
            current = {
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "segment_count": 1,
            }
        else:
            current["end"] = max(current["end"], seg["end"])
            current["text"] += " " + seg["text"]
            current["segment_count"] += 1
    paragraphs.append(current)
    return paragraphs


_EMPTY_TRANSCRIPT = {
    "has_transcript": False,
    "segments": [],
    "words": [],
    "paragraphs": [],
    "full_text": "",
    "duration": 0,
}


def get_youtube_transcript(info: dict) -> dict:
    """
    Извлекает субтитры YouTube без скачивания видео и приводит их к формату Whisper.

    Возвращает полный набор полей (full_text, duration, paragraphs), иначе
    ИИ-анализатор молча возвращал пустой список — он проверяет full_text.
    """
    track = info.get("subtitle_track")
    if not track or not track.get("url"):
        return dict(_EMPTY_TRANSCRIPT)

    try:
        payload = _fetch_subtitle_bytes(track["url"])
    except Exception:
        return dict(_EMPTY_TRANSCRIPT)

    ext = track.get("ext", "json3")
    try:
        if ext == "json3":
            segments, flat_words = _parse_json3(payload)
        else:
            segments, flat_words = _parse_vtt_or_xml(payload, ext)
    except Exception:
        # Иногда ext врёт — пробуем определить формат по содержимому
        try:
            if payload.lstrip().startswith(b"{"):
                segments, flat_words = _parse_json3(payload)
            else:
                segments, flat_words = _parse_vtt_or_xml(payload, "vtt")
        except Exception:
            return dict(_EMPTY_TRANSCRIPT)

    if not segments:
        return dict(_EMPTY_TRANSCRIPT)

    segments.sort(key=lambda s: (s["start"], s["end"]))
    flat_words.sort(key=lambda w: (w["start"], w["end"]))

    full_text = " ".join(s["text"] for s in segments).strip()
    duration = float(info.get("duration") or 0) or max(s["end"] for s in segments)

    return {
        "has_transcript": True,
        "segments": segments,
        "words": flat_words,
        "paragraphs": build_paragraphs(segments),
        "full_text": full_text,
        "duration": round(duration, 2),
        "language": (track.get("lang") or "ru").split("-")[0],
        "language_probability": 1.0,
        "source": "youtube_auto" if track.get("is_auto") else "youtube_manual",
        "word_timestamps_estimated": ext != "json3",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Мгновенные нарезки
# ─────────────────────────────────────────────────────────────────────────────

_SENTENCE_END = (".", "!", "?", "…")

# Целевые длительности Shorts / Reels / TikTok
_MIN_CLIP = 18.0
_TARGET_CLIP = 45.0
_MAX_CLIP = 75.0


def clips_from_transcript(segments: list[dict]) -> list[dict]:
    """
    Собирает клипы из реплик, стараясь резать по границам предложений.

    Прежняя версия просто копила 35 секунд и рвала на полуслове.
    Здесь клип закрывается когда:
      - набралось >= 18 сек И реплика кончилась знаком препинания;
      - либо >= 30 сек и дальше идёт пауза > 1.2 сек;
      - либо достигнут жёсткий предел 75 сек.
    """
    if not segments:
        return []

    clips: list[dict] = []
    bucket: list[dict] = []

    def flush():
        if not bucket:
            return
        start = bucket[0]["start"]
        end = bucket[-1]["end"]
        if end - start < 8.0:
            return
        text = " ".join(s["text"] for s in bucket).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            return
        title = text[:60].rsplit(" ", 1)[0] if len(text) > 60 else text
        clips.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "label": (title or f"Фрагмент {_fmt(start)}").strip(" ,.;:—–-"),
            "description": f"Нарезка по речи ({_fmt(start)} — {_fmt(end)})",
            "score": 0.8,
            "source": "transcript",
            "transcript_text": text,
        })

    for i, seg in enumerate(segments):
        bucket.append(seg)
        span = bucket[-1]["end"] - bucket[0]["start"]
        text = seg["text"].strip()

        next_seg = segments[i + 1] if i + 1 < len(segments) else None
        pause = (next_seg["start"] - seg["end"]) if next_seg else 0.0

        ends_sentence = text.endswith(_SENTENCE_END)
        should_close = (
            (span >= _MIN_CLIP and ends_sentence)
            or (span >= 30.0 and pause > 1.2)
            or span >= _MAX_CLIP
            or next_seg is None
        )

        if should_close:
            flush()
            bucket = []

    flush() if bucket else None
    return clips


def get_instant_clips(info: dict, transcript: dict = None) -> list[dict]:
    """
    Формирует мгновенные клипы при вставке ссылки:
      1. Главы YouTube (самый надёжный источник)
      2. Смысловая нарезка по субтитрам
      3. Равномерные блоки как последний вариант
    """
    chapters = extract_chapters(info)
    if chapters:
        # Слишком длинные главы режем на части под формат Shorts
        adjusted = []
        for ch in chapters:
            span = ch["end"] - ch["start"]
            if span <= _MAX_CLIP * 1.6:
                adjusted.append(ch)
                continue
            pieces = max(2, int(span // _TARGET_CLIP))
            step = span / pieces
            for p in range(pieces):
                p_start = ch["start"] + step * p
                p_end = min(ch["end"], p_start + step)
                if p_end - p_start < 10:
                    continue
                adjusted.append({
                    **ch,
                    "start": round(p_start, 2),
                    "end": round(p_end, 2),
                    "label": f"{ch['label']} ({p + 1}/{pieces})",
                })
        return adjusted

    duration = float(info.get("duration") or 0)

    if transcript and transcript.get("has_transcript") and transcript.get("segments"):
        clips = clips_from_transcript(transcript["segments"])
        if clips:
            return clips

    if duration <= 0:
        return []

    clips = []
    t = 0.0
    idx = 1
    while t < duration:
        end_t = min(duration, t + _TARGET_CLIP)
        if end_t - t >= 15.0:
            clips.append({
                "start": round(t, 2),
                "end": round(end_t, 2),
                "label": f"Момент #{idx} ({_fmt(t)})",
                "description": f"Автоматический фрагмент ({_fmt(t)} — {_fmt(end_t)})",
                "score": 0.7,
                "source": "uniform",
            })
            idx += 1
        t += _TARGET_CLIP

    return clips


# ─────────────────────────────────────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────────────────────────────────────

def _detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "vk.com" in url_lower or "vkvideo.ru" in url_lower:
        return "vk"
    if "tiktok.com" in url_lower:
        return "tiktok"
    if "twitch.tv" in url_lower:
        return "twitch"
    if "rutube.ru" in url_lower:
        return "rutube"
    return "other"


def _fmt(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
