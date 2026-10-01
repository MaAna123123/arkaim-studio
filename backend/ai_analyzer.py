"""
ai_analyzer.py — ИИ-анализ видео для поиска интересных моментов.

Поддерживаемые ИИ-провайдеры:
  1. 🌌 Google Gemini API (Gemini 2.0 Flash)
  2. 🐋 DeepSeek API (deepseek-chat / deepseek-reasoner)
  3. 🆓 OpenRouter — Бесплатные модели (Qwen 2.5 72B, DeepSeek-R1)
  4. 💻 Ollama — 100% Локальный ИИ на ПК (Qwen/DeepSeek без интернета)
  5. ⚡ Локальный fallback (поиск ключевых слов в тексте)
"""

import asyncio
import json
import re
import os
import subprocess
import tempfile
import base64
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Awaitable


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout


def _fmt_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _parse_time(t: str) -> float:
    try:
        parts = [float(x) for x in str(t).split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return float(parts[0])
    except Exception:
        return 0.0


def _call_openai_compatible_api(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_msg: str,
    timeout_sec: float = 90.0,
) -> str:
    """Универсальный клиент для OpenAI-совместимых API (DeepSeek, OpenRouter, Ollama)."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key or 'ollama'}",
    }
    if "openrouter" in base_url:
        headers["HTTP-Referer"] = "http://localhost:5173"
        headers["X-Title"] = "videopril"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
    }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        res_json = json.loads(resp.read().decode("utf-8"))
        return res_json["choices"][0]["message"]["content"]


# ─── Main class ───────────────────────────────────────────────────────────────

class VideoAIAnalyzer:
    def __init__(
        self,
        api_key: str = "",
        provider: str = "gemini",
        model_name: str = "",
        base_url: str = "",
    ):
        self.api_key = api_key
        self.provider = provider  # "gemini" | "deepseek" | "openrouter" | "ollama"
        self.model_name = model_name
        self.base_url = base_url

    # ══════════════════════════════════════════════════════════════════════════
    # МЕТОД 1 — Поиск по транскрипту (Vizard-стиль)
    # ══════════════════════════════════════════════════════════════════════════

    async def analyze_from_transcript(
        self,
        transcript: dict,
        prompt: str,
        on_progress: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> list[dict]:
        async def _progress(msg: str, pct: int):
            if on_progress:
                await on_progress(msg, pct)

        paragraphs = transcript.get("paragraphs", [])
        timed_units = transcript.get("segments", []) or paragraphs
        full_text = transcript.get("full_text", "")
        duration = transcript.get("duration", 0)

        if not full_text.strip():
            await _progress("Транскрипт пуст", 100)
            return []

        # Маршрутизация по провайдерам. If an external model returns too few
        # clips, supplement it with local timestamped candidates.
        if self.provider == "deepseek":
            segments = await self._transcript_openai(
                timed_units, full_text, duration, prompt, _progress,
                base_url=self.base_url or "https://api.deepseek.com/v1",
                model=self.model_name or "deepseek-chat",
                provider_name="DeepSeek AI",
            )
        elif self.provider == "openrouter":
            segments = await self._transcript_openai(
                timed_units, full_text, duration, prompt, _progress,
                base_url=self.base_url or "https://openrouter.ai/api/v1",
                model=self.model_name or "qwen/qwen-2.5-72b-instruct:free",
                provider_name="OpenRouter (Qwen/DeepSeek)",
            )
        elif self.provider == "ollama":
            segments = await self._transcript_openai(
                timed_units, full_text, duration, prompt, _progress,
                base_url=self.base_url or "http://localhost:11434/v1",
                model=self.model_name or "qwen2.5:7b",
                provider_name="Ollama (Локальный ИИ)",
            )
        elif self.api_key:
            segments = await self._transcript_gemini(timed_units, full_text, duration, prompt, _progress)
        else:
            return await self._transcript_local(
                self._group_timed_units(timed_units, max_duration=65.0), prompt, _progress
            )

        segments = self._merge_non_overlapping(segments, [], limit=15)
        if len(segments) < 10:
            local_segments = await self._transcript_local(
                self._group_timed_units(timed_units, max_duration=65.0), prompt, _progress
            )
            segments = self._merge_non_overlapping(segments, local_segments, limit=15)
        return segments[:15]

    # ══════════════════════════════════════════════════════════════════════════
    # Генерация текста для соцсетей (полуавтоматическая публикация)
    # ══════════════════════════════════════════════════════════════════════════

    def _resolve_endpoint(self) -> tuple[str, str]:
        """Возвращает (base_url, model) для текущего OpenAI-совместимого провайдера."""
        if self.provider == "deepseek":
            return self.base_url or "https://api.deepseek.com/v1", self.model_name or "deepseek-chat"
        if self.provider == "openrouter":
            return (self.base_url or "https://openrouter.ai/api/v1",
                    self.model_name or "qwen/qwen-2.5-72b-instruct:free")
        if self.provider == "ollama":
            return self.base_url or "http://localhost:11434/v1", self.model_name or "qwen2.5:7b"
        return "", ""

    async def _chat(self, system_prompt: str, user_msg: str, timeout: float = 60.0) -> str:
        """Единый вызов чат-модели для любого настроенного провайдера."""
        base_url, model = self._resolve_endpoint()

        if base_url:
            return await asyncio.to_thread(
                _call_openai_compatible_api,
                base_url, self.api_key, model, system_prompt, user_msg, timeout,
            )

        if self.api_key:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model_name or "gemini-2.0-flash",
                contents=user_msg,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                ),
            )
            return response.text or ""

        raise RuntimeError("ИИ-провайдер не настроен")

    async def generate_social_copy(
        self,
        text: str,
        label: str = "",
        platform: str = "instagram",
        limits: dict | None = None,
    ) -> dict:
        """
        Генерирует заголовок, описание и хэштеги под конкретную площадку.

        Возвращает {"title": str, "description": str, "hashtags": [str]}.
        """
        limits = limits or {"title": 100, "description": 2200, "max_tags": 12}

        tone = {
            "instagram": "Instagram Reels — эмоционально, живо, с крючком в первой строке",
            "tiktok": "TikTok — коротко, дерзко, разговорным языком, с интригой",
            "youtube": "YouTube Shorts — ёмко и информативно, с понятной пользой",
        }.get(platform, "короткое вертикальное видео")

        system_prompt = (
            "Ты SMM-редактор, который оформляет вертикальные видео для соцсетей. "
            "Пишешь на русском языке, живо и без канцелярита. "
            "Отвечаешь СТРОГО одним JSON-объектом без markdown и пояснений."
        )

        user_msg = (
            f"Площадка: {tone}.\n"
            f"Рабочее название клипа: {label}\n\n"
            f"Расшифровка речи из клипа:\n\"\"\"\n{text}\n\"\"\"\n\n"
            f"Придумай оформление публикации. Верни JSON строго такого вида:\n"
            '{"title": "...", "description": "...", "hashtags": ["тег1", "тег2"]}\n\n'
            f"Требования:\n"
            f"- title: цепляющий заголовок до {limits['title']} символов, без хэштегов и кавычек;\n"
            f"- description: 1–3 предложения по сути клипа, до 400 символов, можно один эмодзи;\n"
            f"- hashtags: {min(limits['max_tags'], 10)} релевантных тегов на русском без символа #, "
            f"по теме видео, без пробелов внутри тега.\n"
            f"Опирайся только на содержание расшифровки, ничего не выдумывай."
        )

        raw = await self._chat(system_prompt, user_msg)
        return self._parse_social_json(raw)

    @staticmethod
    def _parse_social_json(raw: str) -> dict:
        """Достаёт JSON-объект из ответа модели, даже если тот обёрнут в markdown."""
        cleaned = (raw or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}

        tags = data.get("hashtags") or data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]

        return {
            "title": str(data.get("title", "")).strip(),
            "description": str(data.get("description", "")).strip(),
            "hashtags": [str(t).strip().lstrip("#") for t in tags if str(t).strip()],
        }

    async def _transcript_gemini(self, paragraphs, full_text, duration, prompt, progress):
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return await self._transcript_local(self._group_timed_units(paragraphs), prompt, progress)

        await progress("Подключаюсь к Gemini API...", 10)
        client = genai.Client(api_key=self.api_key)

        lines = [f"[{_fmt_time(p['start'])} → {_fmt_time(p['end'])}] {p['text']}" for p in paragraphs]
        transcript_text = "\n".join(lines)
        if len(transcript_text) > 60000:
            transcript_text = transcript_text[:60000] + "\n...[обрезано]"

        await progress("Gemini читает транскрипт...", 35)

        system_prompt = self._get_system_prompt()
        user_msg = f"Запрос: {prompt}\nДлина видео: {_fmt_time(duration)}\n\nТРАНСКРИПТ:\n{transcript_text}"

        await progress("Gemini ищет лучшие моменты...", 60)

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=self.model_name or "gemini-2.0-flash",
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_msg)])],
                config=types.GenerateContentConfig(system_instruction=system_prompt),
            ),
        )

        await progress("Обрабатываю ответ ИИ...", 85)
        return self._parse_ai_json_response(response.text, duration, "transcript_gemini", progress)

    async def _transcript_openai(
        self, paragraphs, full_text, duration, prompt, progress, base_url, model, provider_name
    ):
        """Анализ через DeepSeek / OpenRouter (Qwen) / Ollama."""
        await progress(f"Подключаюсь к {provider_name} ({model})...", 15)

        lines = [f"[{_fmt_time(p['start'])} → {_fmt_time(p['end'])}] {p['text']}" for p in paragraphs]
        transcript_text = "\n".join(lines)
        if len(transcript_text) > 50000:
            transcript_text = transcript_text[:50000] + "\n...[обрезано]"

        system_prompt = self._get_system_prompt()
        user_msg = f"Запрос: {prompt}\nДлина видео: {_fmt_time(duration)}\n\nТРАНСКРИПТ:\n{transcript_text}"

        await progress(f"{provider_name} анализирует текст...", 45)

        try:
            raw_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _call_openai_compatible_api(
                    base_url=base_url,
                    api_key=self.api_key,
                    model=model,
                    system_prompt=system_prompt,
                    user_msg=user_msg,
                )
            )
        except Exception as e:
            await progress(f"Ошибка API ({provider_name}): {str(e)}", 100)
            return await self._transcript_local(self._group_timed_units(paragraphs), prompt, progress)

        await progress("Обрабатываю клипы...", 85)
        return self._parse_ai_json_response(raw_response, duration, f"transcript_{self.provider}", progress)

    def _get_system_prompt(self) -> str:
        return """Ты — топовый контент-стратег и монтажёр YouTube Shorts / TikTok.
Тебе дают транскрипт видео с таймкодами [ЧЧ:ММ:СС → ЧЧ:ММ:СС].
Сам определи тип контента и выбери 10–15 объективно лучших фрагментов без заранее заданной категории.

Критерии Shorts:
- сильный хук в первые секунды, минимум вступления и воды;
- самодостаточная законченная мысль, понятная без контекста;
- юмор, эмоция, конфликт, неожиданность, полезный инсайт или яркая реакция;
- оптимальная длина 20–75 секунд;
- начало и конец должны совпадать с границами реплик из транскрипта;
- фрагменты не должны пересекаться или повторять одну и ту же мысль;
- score ставь строго: 0.9+ только действительно выдающимся моментам.

Правила смысловых границ:
- сначала определи, где начинается тема, вопрос или подводка к истории;
- если рассказ является ответом, включи короткий вопрос собеседника, без которого ответ непонятен;
- затем включи завязку, развитие, поворот или кульминацию и завершение мысли;
- не начинай клип с местоимений вроде «он», «это», «тогда», если без предыдущей реплики непонятно, о чём речь;
- не заканчивай клип посреди предложения, объяснения или до развязки;
- не добавляй соседний разговор после того, как история или ответ уже завершены;
- выбирай границы по таймкодам конкретных реплик, а не по крупным абзацам.

Верни ТОЛЬКО JSON-массив без markdown-блоков и текста вне JSON:
[
  {
    "start_time": "00:47:23",
    "end_time": "00:52:10",
    "label": "Заголовок для Shorts",
    "description": "Почему зайдёт в Shorts",
    "transcript_excerpt": "Полный текст выбранного фрагмента без сокращения...",
    "score": 0.95
  }
]"""

    @staticmethod
    def _group_timed_units(units: list[dict], max_duration: float = 90.0) -> list[dict]:
        """Groups short timestamped phrases for the local fallback scorer."""
        if not units:
            return []

        groups = []
        current = {"start": units[0]["start"], "end": units[0]["end"], "text": units[0]["text"]}
        for unit in units[1:]:
            next_duration = unit["end"] - current["start"]
            if next_duration > max_duration:
                groups.append(current)
                current = {"start": unit["start"], "end": unit["end"], "text": unit["text"]}
            else:
                current["end"] = unit["end"]
                current["text"] += " " + unit["text"]
        groups.append(current)
        return groups

    @staticmethod
    def _merge_non_overlapping(primary: list[dict], fallback: list[dict], limit: int = 15) -> list[dict]:
        chosen = []
        candidates = sorted(primary, key=lambda item: item.get("score", 0), reverse=True)
        candidates.extend(sorted(fallback, key=lambda item: item.get("score", 0), reverse=True))
        for candidate in candidates:
            if len(chosen) >= limit:
                break
            start, end = candidate["start"], candidate["end"]
            overlaps = any(
                max(start, item["start"]) < min(end, item["end"])
                for item in chosen
            )
            if not overlaps:
                chosen.append(candidate)
        return sorted(chosen, key=lambda item: item["start"])

    @staticmethod
    def _local_title(text: str, index: int) -> str:
        clean = " ".join(text.split()).strip(" \"'«»—–-")
        if not clean:
            return f"Интересный момент #{index + 1}"
        sentence = re.split(r"(?<=[.!?])\s+", clean, maxsplit=1)[0]
        words = sentence.split()
        title = " ".join(words[:11]).strip(" ,.;:—–-")
        if len(words) > 11 or len(title) > 78:
            shortened = title[:75].rsplit(" ", 1)[0].rstrip(" ,.;:—–-")
            title = (shortened or title[:75]).rstrip() + "…"
        return title[:80] or f"Интересный момент #{index + 1}"

    def _parse_ai_json_response(self, raw: str, duration: float, source_tag: str, progress) -> list[dict]:
        raw = raw.strip()
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 2 else parts[0]
            if raw.startswith("json\n") or raw.startswith("json\r\n"):
                raw = raw[raw.find("\n") + 1:]
        raw = raw.strip()

        try:
            data = json.loads(raw)
        except Exception:
            # Попытка найти JSON массив внутри текста
            start_idx = raw.find("[")
            end_idx = raw.rfind("]")
            if start_idx != -1 and end_idx != -1:
                data = json.loads(raw[start_idx : end_idx + 1])
            else:
                return []

        if isinstance(data, dict):
            data = data.get("segments", data.get("clips", []))
        if not isinstance(data, list):
            return []

        segments = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if "start_pct" in item or "end_pct" in item:
                try:
                    start = float(item.get("start_pct", 0)) * duration
                    end = float(item.get("end_pct", 0.1)) * duration
                except (TypeError, ValueError):
                    continue
            else:
                start = _parse_time(item.get("start_time", item.get("start", "0:00")))
                end = _parse_time(item.get("end_time", item.get("end", "0:30")))
            start = _clamp(start, 0, duration)
            end = _clamp(end, 0, duration)
            if end <= start:
                end = min(start + 60, duration)
            if end <= start:
                continue
            try:
                score = _clamp(float(item.get("score", 0.8)), 0, 1)
            except (TypeError, ValueError):
                score = 0.8

            segments.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "label": item.get("label", "Интересный момент"),
                "description": item.get("description", ""),
                "score": score,
                "source": source_tag,
                "transcript_text": item.get("transcript_excerpt", ""),
            })

        return sorted(segments, key=lambda x: x["start"])

    async def _transcript_local(self, paragraphs, prompt, progress):
        await progress("Ищу интересные моменты по ключевым словам...", 30)

        story_signals = [
            "однажды", "помню", "случилось", "история", "расскажу",
            "было дело", "представь", "слушай", "короче", "кстати",
            "смотри", "реально", "честно", "серьёзно", "прикол",
            "смешно", "капец", "жесть", "нереально", "никогда не забуду",
            "это было", "тогда я", "вдруг", "внезапно", "не поверишь",
        ]

        scored = []
        for para in paragraphs:
            text = para["text"].lower()
            dur = para["end"] - para["start"]
            if dur < 10 or dur > 180:
                continue

            score = 0.4
            for signal in story_signals:
                if signal in text:
                    score += 0.07

            if 30 <= dur <= 90:
                score += 0.15
            elif 20 <= dur <= 120:
                score += 0.05

            score = min(score, 0.95)
            scored.append((score, para))

        top = sorted(scored, key=lambda x: -x[0])[:15]
        top = sorted(top, key=lambda x: x[1]["start"])

        segments = []
        for i, (score, para) in enumerate(top):
            segments.append({
                "start": round(para["start"], 2),
                "end": round(para["end"], 2),
                "label": self._local_title(para["text"], i),
                "description": "Найден по ключевым словам. Выберите ИИ провайдера (DeepSeek / Gemini / Qwen) в настройках.",
                "score": round(score, 2),
                "source": "transcript_local",
                "transcript_text": para["text"],
            })

        await progress("Готово!", 100)
        return segments

    # ══════════════════════════════════════════════════════════════════════════
    # МЕТОД 2 — Анализ по кадрам (Gemini Vision)
    # ══════════════════════════════════════════════════════════════════════════

    async def analyze(
        self,
        video_path: str,
        prompt: str,
        on_progress: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> list[dict]:
        async def _progress(msg: str, pct: int):
            if on_progress:
                await on_progress(msg, pct)

        if self.api_key:
            return await self._analyze_gemini(video_path, prompt, _progress)
        else:
            return await self._analyze_local(video_path, prompt, _progress)

    async def _analyze_gemini(self, video_path: str, prompt: str, progress) -> list[dict]:
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return await self._analyze_local(video_path, prompt, progress)

        await progress("Подключаюсь к Gemini API...", 10)
        client = genai.Client(api_key=self.api_key)
        duration = self._get_duration(video_path)

        await progress("Извлекаю ключевые кадры...", 20)
        frames_b64 = self._sample_frames(video_path, n=24)

        await progress("Отправляю запрос в Gemini AI...", 40)

        system_prompt = """Ты — профессиональный видеомонтажёр.
Тебе дают кадры из видео. Найди интересные моменты согласно запросу.
Верни ТОЛЬКО JSON-массив:
[{"start_pct":0.05,"end_pct":0.15,"label":"Название","description":"Описание","score":0.9}]
От 3 до 10 моментов. Без markdown."""

        user_msg = f"Запрос: {prompt}\nДлина видео: {duration:.1f} секунд."
        parts = [types.Part.from_text(text=user_msg)]
        for fb64 in frames_b64:
            parts.append(types.Part.from_bytes(data=base64.b64decode(fb64), mime_type="image/jpeg"))

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=self.model_name or "gemini-2.0-flash",
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(system_instruction=system_prompt),
            ),
        )

        await progress("Обрабатываю ответ...", 80)
        return self._parse_ai_json_response(response.text, duration, "frames", progress)

    async def _analyze_local(self, video_path: str, prompt: str, progress) -> list[dict]:
        await progress("Локальный анализ (без API)...", 15)
        duration = self._get_duration(video_path)

        scene_times = self._detect_scene_changes(video_path)
        loud_times = self._detect_audio_peaks(video_path, duration)

        raw_times = sorted(set(scene_times + loud_times))
        merged = self._merge_timestamps(raw_times, duration, min_gap=5.0, clip_len=20.0)

        segments = []
        for i, (start, end) in enumerate(merged[:10]):
            segments.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "label": f"Момент #{i + 1}",
                "description": "Обнаружена смена сцены или аудио-пик. Укажите ИИ провайдера в настройках.",
                "score": round(0.5 + 0.3 * (len(merged) - i) / max(len(merged), 1), 2),
                "source": "local",
                "transcript_text": "",
            })
        return segments

    # ─── Internal utils ────────────────────────────────────────────────────────

    def _get_duration(self, video_path: str) -> float:
        out = _run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", video_path])
        try:
            return float(out.strip())
        except Exception:
            return 0.0

    def _sample_frames(self, video_path: str, n: int = 24) -> list[str]:
        duration = self._get_duration(video_path)
        frames = []
        temp_root = Path(__file__).parent.parent.resolve() / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(dir=str(temp_root))
        try:
            for i in range(n):
                t = (duration / n) * i + (duration / n) * 0.5
                out = os.path.join(tmp_dir, f"f{i:03d}.jpg")
                subprocess.run(
                    ["ffmpeg", "-ss", str(t), "-i", video_path,
                     "-frames:v", "1", "-vf", "scale=512:-1", "-q:v", "4", "-y", out],
                    capture_output=True,
                )
                if os.path.exists(out):
                    with open(out, "rb") as fh:
                        frames.append(base64.b64encode(fh.read()).decode())
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return frames

    def _detect_scene_changes(self, video_path: str) -> list[float]:
        result = subprocess.run(
            ["ffmpeg", "-i", video_path, "-vf", "scdet=threshold=10", "-an", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        times = []
        for line in result.stderr.split("\n"):
            for part in line.split():
                if part.startswith("pts_time:"):
                    try:
                        times.append(float(part.split(":")[1]))
                    except Exception:
                        pass
        return times

    def _detect_audio_peaks(self, video_path: str, duration: float) -> list[float]:
        chunk = 5.0
        n = max(1, int(duration / chunk))
        energies = []
        for i in range(n):
            t = i * chunk
            result = subprocess.run(
                ["ffmpeg", "-ss", str(t), "-i", video_path, "-t", str(chunk),
                 "-af", "astats=metadata=1:reset=1", "-f", "null", "-"],
                capture_output=True, text=True,
            )
            rms_total, count = 0.0, 0
            for line in result.stderr.split("\n"):
                if "RMS level dB" in line:
                    try:
                        val = float(line.split(":")[-1].strip())
                        if val > -100:
                            rms_total += val
                            count += 1
                    except Exception:
                        pass
            energies.append((t, rms_total / count if count else -100))

        if not energies:
            return []
        vals = [e[1] for e in energies]
        mean_e = sum(vals) / len(vals)
        return [t for t, e in energies if e > mean_e + 6]

    def _merge_timestamps(self, times, duration, min_gap=5.0, clip_len=20.0):
        if duration <= 0:
            return []
        if not times:
            n = min(5, max(1, int(duration // clip_len)))
            step = duration / n
            return [(step * i, min(step * i + clip_len, duration)) for i in range(n)]
        segments, prev = [], -min_gap
        for t in times:
            if t - prev >= min_gap:
                start = max(0.0, t - 2.0)
                end = min(duration, start + clip_len)
                segments.append((start, end))
                prev = t
        return segments


def generate_equal_chunks(duration: float, chunk_minutes: float = 4.0) -> list[dict]:
    """
    Разбивает всю длительность видео на последовательные равные фрагменты по chunk_minutes минут.
    Применяется для автоматической нарезки сериалов, фильмов и подкастов.
    """
    if duration <= 0:
        return []

    chunk_sec = max(15.0, float(chunk_minutes) * 60.0)
    results = []
    start = 0.0
    idx = 1

    while start < duration:
        end = min(duration, start + chunk_sec)
        if end - start >= 5.0:
            start_str = _fmt_time(start)
            end_str = _fmt_time(end)
            label = f"Часть {idx} ({start_str} - {end_str})"
            results.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "label": label,
                "description": f"Последовательная нарезка по {chunk_minutes:g} мин ({start_str} — {end_str})",
                "score": 1.0,
                "source": "interval",
            })
            idx += 1
        start = end

    return results

