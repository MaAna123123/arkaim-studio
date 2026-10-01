"""Import timestamped text from SRT files into the internal transcript format."""

import html
import re


_TIMECODE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)
_DISPLAY_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$" )


def _parse_timecode(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _clean_lines(lines: list[str]) -> str:
    cleaned = []
    for line in lines:
        text = html.unescape(re.sub(r"<[^>]+>|\{\\[^}]+\}", "", line)).strip()
        text = re.sub(r"\s+", " ", text)
        if not text or _DISPLAY_TIME_RE.fullmatch(text):
            continue
        if not cleaned or cleaned[-1] != text:
            cleaned.append(text)
    return " ".join(cleaned).strip()


def _estimate_words(text: str, start: float, end: float) -> list[dict]:
    """Spread words within an SRT cue when true word timestamps are unavailable."""
    tokens = re.findall(r"\S+", text)
    if not tokens:
        return []

    duration = max(0.05, end - start)
    weights = [max(1, len(re.sub(r"\W", "", token, flags=re.UNICODE))) for token in tokens]
    total_weight = sum(weights)
    cursor_weight = 0
    words = []
    for token, weight in zip(tokens, weights):
        word_start = start + duration * cursor_weight / total_weight
        cursor_weight += weight
        word_end = start + duration * cursor_weight / total_weight
        words.append({
            "word": token,
            "start": round(word_start, 3),
            "end": round(word_end, 3),
            "probability": 1.0,
            "estimated": True,
        })
    return words


def _build_paragraphs(segments: list[dict], pause_threshold: float = 2.0) -> list[dict]:
    if not segments:
        return []

    paragraphs = []
    current = {
        "start": segments[0]["start"],
        "end": segments[0]["end"],
        "text": segments[0]["text"],
        "segment_count": 1,
    }
    for segment in segments[1:]:
        gap = segment["start"] - current["end"]
        if gap > pause_threshold:
            paragraphs.append(current)
            current = {
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
                "segment_count": 1,
            }
        else:
            current["end"] = max(current["end"], segment["end"])
            if segment["text"] != current["text"]:
                current["text"] += " " + segment["text"]
            current["segment_count"] += 1
    paragraphs.append(current)
    return paragraphs


def parse_srt_bytes(content: bytes, filename: str = "subtitles.srt") -> dict:
    if not content:
        raise ValueError("Файл субтитров пуст")

    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("Не удалось определить кодировку SRT")

    normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", normalized.strip())
    segments = []
    all_words = []

    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        time_index = next((i for i, line in enumerate(lines) if _TIMECODE_RE.search(line)), None)
        if time_index is None:
            continue

        match = _TIMECODE_RE.search(lines[time_index])
        start = _parse_timecode(match.group("start"))
        end = _parse_timecode(match.group("end"))
        text = _clean_lines(lines[time_index + 1:])
        if not text or end <= start:
            continue

        words = _estimate_words(text, start, end)
        segment = {
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "words": words,
        }
        segments.append(segment)
        all_words.extend(words)

    if not segments:
        raise ValueError("В файле не найдено ни одной SRT-реплики с таймкодами")

    segments.sort(key=lambda item: (item["start"], item["end"]))
    all_words.sort(key=lambda item: (item["start"], item["end"]))
    full_text = " ".join(segment["text"] for segment in segments)
    duration = max(segment["end"] for segment in segments)
    covered_seconds = sum(max(0, segment["end"] - segment["start"]) for segment in segments)
    coverage_ratio = min(1.0, covered_seconds / duration) if duration else 0
    words_per_minute = len(all_words) / max(duration / 60, 0.01)
    looks_like_chapters = duration > 120 and (
        words_per_minute < 35 or coverage_ratio < 0.25 or len(segments) < 25
    )

    return {
        "full_text": full_text,
        "language": "ru",
        "language_probability": 1.0,
        "duration": round(duration, 2),
        "segments": segments,
        "words": all_words,
        "paragraphs": _build_paragraphs(segments),
        "source": "srt",
        "source_filename": filename,
        "word_timestamps_estimated": True,
        "transcript_kind": "chapters" if looks_like_chapters else "full",
        "coverage_ratio": round(coverage_ratio, 3),
        "words_per_minute": round(words_per_minute, 1),
    }
