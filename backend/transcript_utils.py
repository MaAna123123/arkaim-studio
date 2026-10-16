"""Pure helpers for transcript summaries, timing, and word extraction."""


_SENTENCE_END_CHARS = (".", "!", "?", "…")


def snap_segments_to_speech(
    segments: list[dict],
    transcript: dict,
    duration: float = 0,
    max_shift: float = 2.5,
) -> list[dict]:
    speech = transcript.get("segments") or []
    if not segments:
        return []

    total = float(duration or transcript.get("duration") or 0)
    result = []
    for segment in segments:
        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue

        if speech:
            best_start = None
            for speech_segment in speech:
                speech_start = float(speech_segment["start"])
                if abs(speech_start - start) <= max_shift:
                    if best_start is None or abs(speech_start - start) < abs(best_start - start):
                        best_start = speech_start
                elif (
                    speech_start < start < float(speech_segment["end"])
                    and start - speech_start <= max_shift
                    and best_start is None
                ):
                    best_start = speech_start
            if best_start is not None:
                start = best_start

            best_end = None
            best_rank = -1.0
            for speech_segment in speech:
                speech_end = float(speech_segment["end"])
                delta = abs(speech_end - end)
                if delta > max_shift:
                    continue
                ends_sentence = str(speech_segment.get("text", "")).strip().endswith(
                    _SENTENCE_END_CHARS
                )
                rank = (2.0 if ends_sentence else 1.0) - delta / (max_shift + 1)
                if rank > best_rank:
                    best_rank, best_end = rank, speech_end
            if best_end is not None and best_end > start + 3:
                end = best_end

        start = max(0.0, start)
        if total > 0:
            end = min(end, total)
        if end - start < 3:
            continue
        adjusted = dict(segment)
        adjusted["start"] = round(start, 2)
        adjusted["end"] = round(end, 2)
        result.append(adjusted)

    result.sort(key=lambda item: item["start"])
    deduped = []
    for segment in result:
        if deduped and segment["start"] < deduped[-1]["end"] - 0.5:
            if segment["end"] <= deduped[-1]["end"] + 0.5:
                continue
            segment["start"] = deduped[-1]["end"]
            if segment["end"] - segment["start"] < 3:
                continue
        deduped.append(segment)
    return deduped


def attach_transcript_text(segments: list[dict], words: list[dict]) -> None:
    if not words:
        return
    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        selected = [
            word.get("word", "")
            for word in words
            if float(word.get("end", 0)) >= start
            and float(word.get("start", 0)) <= end
        ]
        if selected:
            segment["transcript_text"] = " ".join(selected).strip()


def transcript_summary(transcript: dict) -> dict:
    return {
        "has_transcript": bool(transcript.get("has_transcript")),
        "language": transcript.get("language", ""),
        "source": transcript.get("source", ""),
        "segment_count": len(transcript.get("segments") or []),
        "word_count": len(transcript.get("words") or []),
        "is_auto": transcript.get("source") == "youtube_auto",
        "preview": (transcript.get("full_text", "") or "")[:400],
    }


def extract_words_from_json(transcript: dict) -> list[dict]:
    if not transcript:
        return []
    words = transcript.get("words", [])
    if words:
        return words

    flat_words = []
    for segment in transcript.get("segments", []):
        segment_words = segment.get("words", [])
        if segment_words:
            flat_words.extend(segment_words)
            continue
        text = segment.get("text", "").strip()
        start = float(segment.get("start", 0))
        end = float(segment.get("end", 0))
        if text and end > start:
            tokens = [token for token in text.split() if token]
            step = max(0.4, end - start) / max(1, len(tokens))
            for index, token in enumerate(tokens):
                flat_words.append({
                    "word": token,
                    "start": round(start + index * step, 3),
                    "end": round(start + (index + 1) * step, 3),
                })
    return flat_words
