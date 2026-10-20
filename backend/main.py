import json
import logging
import math
import os
import re
import uuid
import asyncio
import urllib.parse
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("videopril")

try:
    from .video_processor import VideoProcessor
    from .ai_analyzer import VideoAIAnalyzer, generate_equal_chunks
    from .youtube_downloader import get_info as yt_get_info, download as yt_download, get_youtube_transcript, get_instant_clips
    from .transcriber import VideoTranscriber
    from .subtitle_parser import parse_srt_bytes
    from .social_uploader import youtube_uploader, instagram_uploader, tiktok_uploader
    from .operation_manager import OperationConflictError, OperationManager
    from .project_store import ProjectStore, UnsupportedProjectSchemaError
    from .transcription_service import TranscriptionService
    from .schemas import (
        AnalyzeRequest, AutoPipelineRequest, ExportRequest, SettingsRequest,
        ProjectEditorPatch, SocialPrepareRequest, SocialUploadRequest,
        TranscribeRequest, YoutubeUrlRequest,
    )
    from .transcript_utils import (
        attach_transcript_text as _attach_transcript_text,
        extract_words_from_json as _extract_words_from_json,
        snap_segments_to_speech,
        transcript_summary as _transcript_summary,
    )
except ImportError:
    from video_processor import VideoProcessor
    from ai_analyzer import VideoAIAnalyzer, generate_equal_chunks
    from youtube_downloader import get_info as yt_get_info, download as yt_download, get_youtube_transcript, get_instant_clips
    from transcriber import VideoTranscriber
    from subtitle_parser import parse_srt_bytes
    from social_uploader import youtube_uploader, instagram_uploader, tiktok_uploader
    from operation_manager import OperationConflictError, OperationManager
    from project_store import ProjectStore, UnsupportedProjectSchemaError
    from transcription_service import TranscriptionService
    from schemas import (
        AnalyzeRequest, AutoPipelineRequest, ExportRequest, SettingsRequest,
        ProjectEditorPatch, SocialPrepareRequest, SocialUploadRequest,
        TranscribeRequest, YoutubeUrlRequest,
    )
    from transcript_utils import (
        attach_transcript_text as _attach_transcript_text,
        extract_words_from_json as _extract_words_from_json,
        snap_segments_to_speech,
        transcript_summary as _transcript_summary,
    )

# ─────────────────────────────────────────────
# Force ALL temp files & model caches to Drive X:
# ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BASE_DIR = PROJECT_ROOT
TEMP_DIR = BASE_DIR / "temp"
MODELS_DIR = BASE_DIR / "models_cache"

for d in [TEMP_DIR, MODELS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

os.environ["TEMP"] = str(TEMP_DIR)
os.environ["TMP"] = str(TEMP_DIR)
os.environ["TMPDIR"] = str(TEMP_DIR)
os.environ["HF_HOME"] = str(MODELS_DIR)

# App setup
app = FastAPI(title="videopril API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories are anchored to the project, so startup does not depend on cwd.
BACKEND_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = BACKEND_DIR / "uploads"
CLIPS_DIR = BACKEND_DIR / "clips"
THUMBS_DIR = BACKEND_DIR / "thumbs"
TRANSCRIPTS_DIR = BACKEND_DIR / "transcripts"
ADS_DIR = BACKEND_DIR / "ads"
BANNER_DIR = BACKEND_DIR / "banners"
PROJECTS_DIR = PROJECT_ROOT / "data" / "projects"

for d in [UPLOAD_DIR, CLIPS_DIR, THUMBS_DIR, TRANSCRIPTS_DIR, ADS_DIR, BANNER_DIR, TEMP_DIR, MODELS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")
app.mount("/thumbs", StaticFiles(directory=str(THUMBS_DIR)), name="thumbs")

# Global state
processor = VideoProcessor()

# Настройки хранятся на диске, иначе введённые API-ключи пропадали при
# каждом перезапуске сервера и анализ молча откатывался на слабый локальный режим.
SETTINGS_FILE = BACKEND_DIR / "settings.json"
_DEFAULT_SETTINGS = {
    "provider": "gemini",
    "api_key": "",
    "model_name": "",
    "base_url": "",
    "whisper_model": "small",
}


def _load_settings() -> dict:
    data = dict(_DEFAULT_SETTINGS)
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                data.update({k: v for k, v in stored.items() if k in _DEFAULT_SETTINGS})
    except Exception:
        pass
    return data


def _save_settings() -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings_store, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning(f"Не удалось сохранить настройки: {exc}")


settings_store: dict = _load_settings()

# Whisper нужен только как запасной вариант, когда у видео нет субтитров,
# поэтому модель загружается лениво — при старте сервера её ждать не надо.
transcriber = VideoTranscriber(model_size=settings_store.get("whisper_model", "small"))
operation_manager = OperationManager()
project_store = ProjectStore(
    PROJECT_ROOT,
    legacy_transcripts_dir=TRANSCRIPTS_DIR,
)
active_connections: list[WebSocket] = []

# ─────────────────────────────────────────────
# WebSocket manager
# ─────────────────────────────────────────────
async def broadcast(message: dict):
    dead = []
    for ws in active_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        active_connections.remove(ws)


transcription_service = TranscriptionService(
    operation_manager,
    transcriber,
    broadcast,
    project_store,
)


@app.on_event("startup")
async def recover_transcription_operations():
    """Restore saved operations as stopped without scheduling Whisper work."""
    try:
        recovered = transcription_service.recover_projects()
    except (OSError, ValueError, json.JSONDecodeError):
        logger.exception("Не удалось восстановить сохранённые транскрипции")
        return
    if recovered:
        logger.info("Восстановлено операций транскрипции: %d", len(recovered))


@app.on_event("shutdown")
async def shutdown_transcription_workers():
    await transcription_service.shutdown()

@app.websocket("/ws/progress")
async def ws_progress(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if websocket in active_connections:
            active_connections.remove(websocket)

# ─────────────────────────────────────────────
# Health & Settings
# ─────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "ffmpeg": processor.check_ffmpeg()}


def _normalize_project_id(project_id: str) -> str:
    try:
        return str(uuid.UUID(project_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Некорректный идентификатор проекта",
        ) from exc


def _project_storage_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="Проект не найден")
    logger.exception("Не удалось прочитать проект", exc_info=exc)
    return HTTPException(status_code=500, detail="Не удалось прочитать данные проекта")


def _checkpoint_diagnostic(code: str) -> dict:
    return {
        "resume_available": False,
        "diagnostic": {
            "code": code,
            "message": "Сохранённый прогресс нельзя продолжить",
        },
    }


def _public_operation_snapshot(operation: dict) -> dict:
    partial_result = operation.get("partial_result")
    checkpoint = (
        partial_result.get("checkpoint")
        if isinstance(partial_result, dict)
        else None
    )
    if isinstance(checkpoint, bool) or not isinstance(checkpoint, (int, float)):
        checkpoint = None
    return {
        "operation_id": operation["operation_id"],
        "status": operation["status"],
        "progress_pct": operation["progress_pct"],
        "checkpoint": checkpoint,
        "resume_available": operation["status"] == "stopped",
    }


def _validated_checkpoint(project_id: str, checkpoint: object) -> dict | None:
    if not isinstance(checkpoint, dict):
        return None
    try:
        normalized = OperationManager().restore(checkpoint)
    except (TypeError, ValueError):
        return None
    if normalized is None or normalized.get("video_id") != project_id:
        return None
    return normalized


def _checkpoint_public_snapshot(project_id: str) -> dict | None:
    operation = operation_manager.find_for_video(project_id, include_stopped=True)
    if operation is not None:
        return _public_operation_snapshot(operation)
    try:
        checkpoint = project_store.load_checkpoint(project_id)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        return _checkpoint_diagnostic("checkpoint_unreadable")
    if checkpoint is None:
        return None
    if _validated_checkpoint(project_id, checkpoint) is None:
        return _checkpoint_diagnostic("checkpoint_invalid")
    return _checkpoint_diagnostic("checkpoint_not_recovered")


def _project_transcription_snapshot(project_id: str, transcript: dict | None) -> dict | None:
    """Use one precedence rule for project list and detail responses."""
    if transcript is not None:
        return None
    return _checkpoint_public_snapshot(project_id)


def _public_project(project_id: str) -> dict:
    project = project_store.get_project(project_id)
    project.pop("source_path", None)
    source = project_store.source_status(project_id)
    transcript = project_store.load_transcript(project_id)
    project.update(
        source_exists=bool(source.get("exists")),
        source_diagnostic=source.get("diagnostic"),
        has_transcript=transcript is not None,
        transcription=_project_transcription_snapshot(project_id, transcript),
    )
    return project


@app.get("/api/projects")
async def list_projects(limit: int = 20):
    if not 1 <= limit <= 20:
        raise HTTPException(status_code=400, detail="limit должен быть от 1 до 20")
    try:
        listing = project_store.list_projects(limit=limit)
        for summary in listing["projects"]:
            project_id = summary["project_id"]
            try:
                transcript = project_store.load_transcript(project_id)
            except (OSError, ValueError, json.JSONDecodeError):
                transcript = None
            summary["has_transcript"] = transcript is not None
            summary["transcription"] = _project_transcription_snapshot(
                project_id,
                transcript,
            )
        return listing
    except (OSError, ValueError, json.JSONDecodeError,
            UnsupportedProjectSchemaError) as exc:
        raise _project_storage_error(exc) from exc


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    normalized_id = _normalize_project_id(project_id)
    try:
        return _public_project(normalized_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError,
            UnsupportedProjectSchemaError) as exc:
        raise _project_storage_error(exc) from exc


@app.patch("/api/projects/{project_id}")
async def patch_project(project_id: str, payload: dict):
    normalized_id = _normalize_project_id(project_id)
    try:
        request = ProjectEditorPatch.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="Разрешены только поля состояния редактора проекта",
        ) from exc
    try:
        project_store.get_project(normalized_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError,
            UnsupportedProjectSchemaError) as exc:
        raise _project_storage_error(exc) from exc
    try:
        updates = request.model_dump(exclude_unset=True)
        project_store.update_editor(normalized_id, updates)
        return _public_project(normalized_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (FileNotFoundError, OSError, json.JSONDecodeError,
            UnsupportedProjectSchemaError) as exc:
        raise _project_storage_error(exc) from exc


@app.post("/api/projects/{project_id}/transcription/restart")
async def restart_project_transcription(project_id: str):
    normalized_id = _normalize_project_id(project_id)
    try:
        project = project_store.get_project(normalized_id)
        source = project_store.source_status(normalized_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError,
            UnsupportedProjectSchemaError) as exc:
        raise _project_storage_error(exc) from exc

    if not source.get("exists"):
        raise HTTPException(
            status_code=409,
            detail="Исходный файл проекта не найден; сохранённые данные не изменены",
        )

    existing = operation_manager.find_for_video(normalized_id, include_stopped=True)
    if existing is not None and existing["status"] != "stopped":
        raise HTTPException(
            status_code=409,
            detail="Текущая транскрипция ещё активна",
        )

    try:
        source_path = Path(project["source_path"])
        if not source_path.is_absolute():
            source_path = Path(project_store.project_root) / source_path
        operation = transcription_service.restart_from_zero(
            normalized_id,
            str(source_path),
            None,
            TRANSCRIPTS_DIR / f"{normalized_id}.json",
            asyncio.get_running_loop(),
            existing["operation_id"] if existing is not None else None,
        )
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError, json.JSONDecodeError,
            UnsupportedProjectSchemaError) as exc:
        raise _project_storage_error(exc) from exc
    return JSONResponse(content=operation, status_code=202)

@app.get("/api/settings")
async def get_settings():
    key = settings_store.get("api_key", "")
    masked = ("*" * (len(key) - 4) + key[-4:]) if len(key) > 4 else ("*" * len(key))
    return {
        "provider": settings_store.get("provider", "gemini"),
        "api_key_set": bool(key),
        "api_key_masked": masked,
        "model_name": settings_store.get("model_name", ""),
        "base_url": settings_store.get("base_url", ""),
        "whisper_model": settings_store.get("whisper_model", "small"),
    }


def _guess_provider(api_key: str) -> Optional[str]:
    """
    Определяет провайдера по формату ключа, чтобы пользователю не нужно было
    вручную переключать провайдера при вставке ключа.
    """
    key = (api_key or "").strip()
    if not key:
        return None
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-"):
        return "deepseek"
    if key.startswith("AIza"):
        return "gemini"
    return None


@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    key_val = (req.api_key or req.gemini_api_key or "").strip()

    if key_val:
        settings_store["api_key"] = key_val

    # Явно выбранный провайдер важнее автоопределения
    explicit_provider = (req.provider or "").strip().lower()
    if explicit_provider and explicit_provider != "auto":
        settings_store["provider"] = explicit_provider
    elif key_val:
        guessed = _guess_provider(key_val)
        if guessed:
            settings_store["provider"] = guessed

    if req.model_name is not None and req.model_name.strip():
        settings_store["model_name"] = req.model_name.strip()
    if req.base_url is not None and req.base_url.strip():
        settings_store["base_url"] = req.base_url.strip()

    if req.whisper_model and req.whisper_model in {"tiny", "base", "small", "medium", "large-v3"}:
        if req.whisper_model != settings_store.get("whisper_model"):
            settings_store["whisper_model"] = req.whisper_model
            transcriber.set_model_size(req.whisper_model)

    _save_settings()
    return {
        "success": True,
        "provider": settings_store.get("provider"),
        "api_key_set": bool(settings_store.get("api_key")),
    }


def _build_analyzer() -> VideoAIAnalyzer:
    return VideoAIAnalyzer(
        api_key=settings_store.get("api_key", ""),
        provider=settings_store.get("provider", "gemini"),
        model_name=settings_store.get("model_name", ""),
        base_url=settings_store.get("base_url", ""),
    )


_DEFAULT_ANALYZE_PROMPT = (
    "Самостоятельно найди лучшие законченные истории и смысловые моменты. "
    "Ищи полную смысловую дугу: вопрос или подводку, начало рассказа, развитие, "
    "кульминацию и вывод. Если история началась с вопроса собеседника, включи вопрос "
    "в начало клипа. Не вырывай яркую фразу без контекста. Убери воду до начала темы "
    "и после завершения мысли. Оцени юмор, эмоции, конфликт, неожиданность, пользу и виральность."
)

# ─────────────────────────────────────────────
# Подгонка границ клипов под речь
# ─────────────────────────────────────────────
_SENTENCE_END_CHARS = (".", "!", "?", "…")


def _legacy_snap_segments_to_speech(
    segments: list[dict],
    transcript: dict,
    duration: float = 0,
    max_shift: float = 2.5,
) -> list[dict]:
    """
    Двигает границы клипа к ближайшей паузе в речи, чтобы клип не начинался
    и не обрывался на полуслове.

    Начало сдвигается к началу ближайшей реплики, конец — к концу реплики,
    желательно завершающейся знаком препинания. Сдвиг ограничен max_shift
    секундами, иначе ИИ-разметка «уплывала» на соседнюю мысль.
    """
    speech = transcript.get("segments") or []
    if not segments:
        return []

    total = float(duration or transcript.get("duration") or 0)
    result = []

    for seg in segments:
        try:
            start = float(seg.get("start", 0))
            end = float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue

        if speech:
            # Начало → начало реплики, которая накрывает или следует за точкой
            best_start = None
            for sp in speech:
                sp_start = float(sp["start"])
                if abs(sp_start - start) <= max_shift:
                    if best_start is None or abs(sp_start - start) < abs(best_start - start):
                        best_start = sp_start
                # реплика уже началась до start и продолжается — режем по её началу
                elif sp_start < start < float(sp["end"]) and start - sp_start <= max_shift:
                    if best_start is None:
                        best_start = sp_start
            if best_start is not None:
                start = best_start

            # Конец → конец реплики, предпочитая завершённое предложение
            best_end = None
            best_rank = -1.0
            for sp in speech:
                sp_end = float(sp["end"])
                delta = abs(sp_end - end)
                if delta > max_shift:
                    continue
                ends_sentence = str(sp.get("text", "")).strip().endswith(_SENTENCE_END_CHARS)
                rank = (2.0 if ends_sentence else 1.0) - delta / (max_shift + 1)
                if rank > best_rank:
                    best_rank, best_end = rank, sp_end
            if best_end is not None and best_end > start + 3:
                end = best_end

        start = max(0.0, start)
        if total > 0:
            end = min(end, total)
        if end - start < 3:
            continue

        new_seg = dict(seg)
        new_seg["start"] = round(start, 2)
        new_seg["end"] = round(end, 2)
        result.append(new_seg)

    # Убираем пересечения после сдвига границ
    result.sort(key=lambda s: s["start"])
    deduped = []
    for seg in result:
        if deduped and seg["start"] < deduped[-1]["end"] - 0.5:
            # Полностью вложенный или почти совпадающий фрагмент отбрасываем
            if seg["end"] <= deduped[-1]["end"] + 0.5:
                continue
            seg["start"] = deduped[-1]["end"]
            if seg["end"] - seg["start"] < 3:
                continue
        deduped.append(seg)

    return deduped


def _legacy_attach_transcript_text(segments: list[dict], words: list[dict]) -> None:
    """Прикрепляет к каждому фрагменту его текст — нужен для подписей и хэштегов."""
    if not words:
        return
    for segment in segments:
        try:
            s_start = float(segment["start"])
            s_end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        selected = [
            w.get("word", "")
            for w in words
            if float(w.get("end", 0)) >= s_start and float(w.get("start", 0)) <= s_end
        ]
        if selected:
            segment["transcript_text"] = " ".join(selected).strip()

# ─────────────────────────────────────────────
# YouTube endpoints
# ─────────────────────────────────────────────
def _legacy_transcript_summary(transcript: dict) -> dict:
    """Краткая сводка вместо полного транскрипта — он может весить мегабайты."""
    return {
        "has_transcript": bool(transcript.get("has_transcript")),
        "language": transcript.get("language", ""),
        "source": transcript.get("source", ""),
        "segment_count": len(transcript.get("segments") or []),
        "word_count": len(transcript.get("words") or []),
        "is_auto": transcript.get("source") == "youtube_auto",
        "preview": (transcript.get("full_text", "") or "")[:400],
    }


def _load_saved_transcript(video_id: str) -> dict | None:
    """Prefer the canonical project transcript, retaining legacy-only records."""
    try:
        normalized_id = str(uuid.UUID(video_id))
    except (AttributeError, TypeError, ValueError):
        normalized_id = None

    if normalized_id is not None:
        try:
            transcript = project_store.load_transcript(normalized_id)
        except FileNotFoundError:
            transcript = None
        except (OSError, ValueError, json.JSONDecodeError,
                UnsupportedProjectSchemaError) as exc:
            raise _project_storage_error(exc) from exc
        if transcript is not None:
            return transcript

    legacy_id = normalized_id or video_id
    transcript_file = TRANSCRIPTS_DIR / f"{legacy_id}.json"
    if not transcript_file.is_file():
        return None
    with transcript_file.open("r", encoding="utf-8") as transcript_stream:
        return json.load(transcript_stream)


def _save_project_transcript_if_present(video_id: str, transcript: dict) -> None:
    """Persist canonical transcript data when this legacy video has a project."""
    try:
        normalized_id = str(uuid.UUID(video_id))
    except (AttributeError, TypeError, ValueError):
        return
    try:
        project_store.save_transcript(normalized_id, transcript)
    except FileNotFoundError:
        return
    except (OSError, ValueError, json.JSONDecodeError,
            UnsupportedProjectSchemaError) as exc:
        raise _project_storage_error(exc) from exc


def _record_ingestion(
    *,
    video_id: str,
    source_path: str | Path,
    source_filename: str,
    title: str,
    metadata: dict,
    source_info: dict | None = None,
    transcript: dict | None = None,
    editor: dict | None = None,
) -> dict:
    """Persist one successful ingestion after its source and metadata exist."""
    source = Path(source_path)
    if not source.is_file():
        raise ValueError("Исходный файл не найден после загрузки")
    if not isinstance(metadata, dict):
        raise ValueError("Не удалось получить метаданные видео")

    video = dict(source_info or {})
    video.update(metadata)
    project = project_store.create_project(
        video_id,
        title,
        source_filename,
        source,
        video,
    )
    if transcript and transcript.get("has_transcript"):
        project_store.save_transcript(video_id, transcript)
    if editor:
        project = project_store.update_editor(video_id, editor)
    return project


@app.post("/api/youtube/info")
async def youtube_info(req: YoutubeUrlRequest):
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(yt_get_info, req.url),
            timeout=30.0,
        )
        transcript = await asyncio.wait_for(
            asyncio.to_thread(get_youtube_transcript, info),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail="Таймаут соединения с YouTube. Возможно, доступ ограничен провайдером. "
                   "Загрузите файл с ПК через вкладку «Видео с ПК».",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    instant_clips = get_instant_clips(info, transcript)

    # Полный транскрипт в ответ не кладём — только сводку
    info.pop("subtitle_track", None)
    info["instant_clips"] = instant_clips
    info["transcript_info"] = _transcript_summary(transcript)
    info["has_transcript"] = bool(transcript.get("has_transcript"))
    return info


async def _resolve_transcript(
    url: str,
    video_id: str,
    video_path: str,
    info: dict,
    transcript: dict,
    progress,
) -> dict:
    """
    Получает транскрипт по стратегии «сначала субтитры YouTube, Whisper — запасной вариант».

    Субтитры YouTube приходят за пару секунд и уже содержат пословные таймкоды,
    поэтому запускать Whisper при их наличии смысла нет.
    """
    if transcript and transcript.get("has_transcript"):
        source = "автоматические" if transcript.get("source") == "youtube_auto" else "ручные"
        await progress(f"Субтитры YouTube получены ({source}, {len(transcript['segments'])} реплик)", 45)
        return transcript

    await progress("Субтитров у видео нет — распознаю речь через Whisper...", 45)
    loop = asyncio.get_running_loop()

    def sync_progress(msg: str, pct: int):
        scaled = 45 + int(pct * 0.25)
        asyncio.run_coroutine_threadsafe(
            broadcast({"type": "progress", "step": "transcribing", "pct": scaled, "msg": msg}),
            loop,
        )

    try:
        return await asyncio.to_thread(
            transcriber.transcribe, video_path, None, sync_progress
        )
    except Exception as exc:
        logger.warning(f"Whisper не смог распознать речь: {exc}")
        return {"has_transcript": False, "segments": [], "words": [],
                "paragraphs": [], "full_text": "", "duration": info.get("duration", 0)}


@app.post("/api/youtube/auto")
async def youtube_auto(req: AutoPipelineRequest):
    """
    Полный автоматический конвейер в одном запросе:
    ссылка → метаданные → субтитры → скачивание → ИИ-анализ → нарезка клипов.

    Раньше это требовало четырёх отдельных вызовов с фронтенда, и любой сбой
    в середине оставлял пользователя с пустым плеером без клипов.
    """
    video_id = str(uuid.uuid4())

    async def progress(msg: str, pct: int, step: str = "auto"):
        await broadcast({"type": "progress", "step": step, "pct": pct, "msg": msg})

    try:
        # 1. Метаданные и субтитры (быстро, до скачивания видео)
        await progress("Читаю информацию о видео...", 5)
        info = await asyncio.wait_for(asyncio.to_thread(yt_get_info, req.url), timeout=40.0)

        await progress("Ищу субтитры...", 12)
        try:
            transcript = await asyncio.wait_for(
                asyncio.to_thread(get_youtube_transcript, info), timeout=30.0
            )
        except asyncio.TimeoutError:
            transcript = {"has_transcript": False, "segments": [], "words": []}

        # 2. Скачивание видеофайла сразу под нужным именем
        await progress("Скачиваю видео...", 18)
        loop = asyncio.get_running_loop()

        def dl_progress(msg: str, pct: int):
            scaled = 18 + int(pct * 0.22)
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "progress", "step": "downloading", "pct": scaled, "msg": msg}),
                loop,
            )

        out_file = await asyncio.to_thread(
            yt_download, req.url, str(UPLOAD_DIR), req.quality, video_id, dl_progress
        )
        video_path = str(out_file)

        # 3. Транскрипт: субтитры YouTube, иначе Whisper
        transcript = await _resolve_transcript(
            req.url, video_id, video_path, info, transcript, progress
        )
        # 4. Превью для таймлайна
        meta = await asyncio.to_thread(processor.extract_metadata, video_path)
        thumbs = await asyncio.to_thread(
            processor.extract_thumbnails, video_path, str(THUMBS_DIR), video_id, 20
        )

        # 5. ИИ-анализ (при наличии речи) либо главы/равномерная нарезка
        await progress("Ищу лучшие моменты...", 72, "analyzing")
        segments = []
        if transcript.get("has_transcript") and transcript.get("full_text", "").strip():
            analyzer = _build_analyzer()

            async def ai_progress(msg: str, pct: int):
                await broadcast({"type": "progress", "step": "analyzing",
                                 "pct": 72 + int(pct * 0.13), "msg": msg})

            prompt = req.prompt.strip() or _DEFAULT_ANALYZE_PROMPT
            try:
                segments = await analyzer.analyze_from_transcript(transcript, prompt, ai_progress)
            except Exception as exc:
                logger.warning(f"ИИ-анализ не удался, беру запасной вариант: {exc}")
                segments = []

        if not segments:
            await progress("Формирую нарезки по главам и речи...", 82, "analyzing")
            segments = get_instant_clips(info, transcript)

        if not segments:
            raise HTTPException(status_code=422, detail="Не удалось выделить фрагменты в этом видео")

        # Подгоняем границы под конец фразы и ограничиваем количество
        segments = snap_segments_to_speech(segments, transcript, meta.get("duration", 0))
        segments = segments[: max(1, req.max_clips)]
        _attach_transcript_text(segments, transcript.get("words") or [])

        # 6. Нарезка
        await progress(f"Нарезаю {len(segments)} клипов...", 86, "exporting")
        clips = await _export_segments(
            video_id=video_id,
            video_path=video_path,
            segments=segments,
            mode="separate",
            padding=req.padding,
            aspect_ratio=req.aspect_ratio,
            burn_subtitles=req.burn_subtitles,
            insert_ad=req.insert_ad,
            words=_extract_words_from_json(transcript),
            text_ad_enabled=req.text_ad_enabled,
            text_ad_text=req.text_ad_text,
            text_ad_position=req.text_ad_position,
            text_ad_interval=req.text_ad_interval,
            text_ad_opacity=req.text_ad_opacity,
            base_pct=86,
            span_pct=14,
        )

        _record_ingestion(
            video_id=video_id,
            source_path=video_path,
            source_filename=Path(video_path).name,
            title=info.get("title", "YouTube video"),
            metadata=meta,
            source_info=info,
            transcript=transcript,
            editor={
                "segments": segments,
                "selected": list(range(len(segments))),
                "clip_results": clips,
                "export_padding": req.padding,
                "aspect_ratio": req.aspect_ratio,
                "burn_subtitles": req.burn_subtitles,
                "insert_ad": req.insert_ad,
                "text_ad_enabled": req.text_ad_enabled,
                "text_ad_text": req.text_ad_text,
                "text_ad_position": req.text_ad_position,
                "text_ad_interval": req.text_ad_interval,
                "text_ad_opacity": req.text_ad_opacity,
            },
        )

        await progress(f"Готово — {len(clips)} клипов!", 100, "done")

        return {
            "video_id": video_id,
            "filename": info.get("title", "YouTube video"),
            "video_url": f"/uploads/{Path(video_path).name}",
            "metadata": meta,
            "thumbnails": thumbs,
            "chapters": segments,
            "instant_clips": segments,
            "segments": segments,
            "clips": clips,
            "transcript": transcript,
            "transcript_info": _transcript_summary(transcript),
            "title": info.get("title", ""),
            "auto": True,
        }

    except HTTPException:
        await broadcast({"type": "progress", "step": "error", "pct": 100, "msg": "Ошибка обработки"})
        raise
    except asyncio.TimeoutError:
        await broadcast({"type": "progress", "step": "error", "pct": 100, "msg": "Таймаут"})
        raise HTTPException(status_code=408, detail="YouTube не ответил вовремя. Проверьте соединение или загрузите файл с ПК.")
    except Exception as e:
        logger.exception("Авто-конвейер упал")
        await broadcast({"type": "progress", "step": "error", "pct": 100, "msg": f"Ошибка: {e}"})
        raise HTTPException(status_code=500, detail=f"Ошибка обработки: {e}")


@app.post("/api/youtube/download")
async def youtube_download(req: YoutubeUrlRequest):
    video_id = str(uuid.uuid4())
    await broadcast({"type": "progress", "step": "downloading", "pct": 10, "msg": "Загрузка информации и субтитров YouTube..."})

    try:
        info = await asyncio.to_thread(yt_get_info, req.url)
        transcript = await asyncio.to_thread(get_youtube_transcript, info)
        instant_clips = get_instant_clips(info, transcript)

        await broadcast({"type": "progress", "step": "downloading", "pct": 30, "msg": "Загрузка видеофайла с YouTube..."})

        loop = asyncio.get_running_loop()

        def dl_progress(msg: str, pct: int):
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "progress", "step": "downloading",
                           "pct": 30 + int(pct * 0.55), "msg": msg}),
                loop,
            )

        # Файл сразу сохраняется под именем video_id — os.rename больше не нужен
        target_path = Path(await asyncio.to_thread(
            yt_download, req.url, str(UPLOAD_DIR), req.quality, video_id, dl_progress
        ))

        await broadcast({"type": "progress", "step": "downloading", "pct": 90, "msg": "Генерация превью..."})

        meta = await asyncio.to_thread(processor.extract_metadata, str(target_path))
        thumbs = await asyncio.to_thread(
            processor.extract_thumbnails, str(target_path), str(THUMBS_DIR), video_id, 20
        )

        _record_ingestion(
            video_id=video_id,
            source_path=target_path,
            source_filename=info.get("title", "YouTube video") + target_path.suffix,
            title=info.get("title", "YouTube video"),
            metadata=meta,
            source_info=info,
            transcript=transcript,
            editor={"segments": instant_clips},
        )

        await broadcast({"type": "progress", "step": "done", "pct": 100, "msg": "Видео готово!"})

        return {
            "video_id": video_id,
            "filename": info.get("title", "YouTube video") + target_path.suffix,
            "video_url": f"/uploads/{target_path.name}",
            "metadata": meta,
            "thumbnails": thumbs,
            "chapters": instant_clips,
            "instant_clips": instant_clips,
            "transcript": transcript,
            "transcript_info": _transcript_summary(transcript),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка обработки: {str(e)}")

# ─────────────────────────────────────────────
# Local File Upload
# ─────────────────────────────────────────────
@app.post("/api/video/upload")
async def upload_video(file: UploadFile = File(...)):
    video_id = str(uuid.uuid4())
    ext = Path(file.filename or "").suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"}:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат видео")
    video_path = UPLOAD_DIR / f"{video_id}{ext}"

    try:
        async with aiofiles.open(video_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                await f.write(chunk)
        if video_path.stat().st_size == 0:
            raise ValueError("Загружен пустой файл")

        meta = await asyncio.to_thread(processor.extract_metadata, str(video_path))
        thumbs = await asyncio.to_thread(
            processor.extract_thumbnails, str(video_path), str(THUMBS_DIR), video_id, 20
        )
    except Exception as exc:
        video_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Не удалось обработать видео: {exc}") from exc
    finally:
        await file.close()

    _record_ingestion(
        video_id=video_id,
        source_path=video_path,
        source_filename=file.filename or video_path.name,
        title=Path(file.filename or video_path.name).stem,
        metadata=meta,
    )

    return {
        "video_id": video_id,
        "filename": file.filename,
        "video_url": f"/uploads/{video_path.name}",
        "metadata": meta,
        "thumbnails": thumbs,
        "chapters": [],
    }

# ─────────────────────────────────────────────
# Transcription (Whisper)
# ─────────────────────────────────────────────
@app.post("/api/video/transcribe")
async def transcribe_video(req: TranscribeRequest):
    video_path = _find_video(req.video_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="Video not found")

    transcript_file = TRANSCRIPTS_DIR / f"{req.video_id}.json"
    transcript = _load_saved_transcript(req.video_id)
    if transcript is not None:
        return transcript

    existing = operation_manager.find_for_video(req.video_id, include_stopped=True)
    operation = transcription_service.start(
        req.video_id,
        str(video_path),
        req.language,
        transcript_file,
        asyncio.get_running_loop(),
    )
    if operation["status"] == "completed":
        transcript = _load_saved_transcript(req.video_id)
        if transcript is not None:
            return transcript
    return JSONResponse(
        content=operation,
        status_code=200 if existing is not None else 202,
    )


@app.get("/api/operations")
async def list_operations(video_id: str | None = None, active: bool = False):
    return {
        "operations": operation_manager.list_operations(
            video_id=video_id,
            active_only=active,
        )
    }


@app.get("/api/operations/{operation_id}")
async def get_operation(operation_id: str):
    operation = operation_manager.get(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    return operation


def _run_operation_action(operation_id: str, action: str) -> dict:
    if operation_manager.get(operation_id) is None:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    try:
        if action == "pause":
            return transcription_service.pause(operation_id)
        if action == "resume":
            return transcription_service.resume(operation_id, asyncio.get_running_loop())
        if action == "stop":
            return transcription_service.stop(operation_id)
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=404, detail="Неизвестная команда операции")


@app.post("/api/operations/{operation_id}/pause")
async def pause_operation(operation_id: str):
    return _run_operation_action(operation_id, "pause")


@app.post("/api/operations/{operation_id}/resume")
async def resume_operation(operation_id: str):
    return _run_operation_action(operation_id, "resume")


@app.post("/api/operations/{operation_id}/stop")
async def stop_operation(operation_id: str):
    return _run_operation_action(operation_id, "stop")


@app.post("/api/video/transcript/import")
async def import_transcript(video_id: str = Form(...), file: UploadFile = File(...)):
    if not _find_video(video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    if not (file.filename or "").lower().endswith(".srt"):
        raise HTTPException(status_code=400, detail="Сейчас поддерживается импорт файлов .srt")

    try:
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("SRT-файл слишком большой (максимум 10 МБ)")
        data = parse_srt_bytes(content, file.filename or "subtitles.srt")
        is_chapter_map = data.get("transcript_kind") == "chapters"
        target_suffix = ".chapters.json" if is_chapter_map else ".json"
        if not is_chapter_map:
            _save_project_transcript_if_present(video_id, data)
        transcript_file = TRANSCRIPTS_DIR / f"{video_id}{target_suffix}"
        with open(transcript_file, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
        data["has_transcript"] = not is_chapter_map
        data["chapter_context_loaded"] = is_chapter_map
        return data
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()

@app.get("/api/video/transcript/{video_id}")
async def get_transcript(video_id: str):
    data = _load_saved_transcript(video_id)
    if data is None:
        return {"has_transcript": False}
    data["has_transcript"] = True
    return data

# ─────────────────────────────────────────────
# Analyze
# ─────────────────────────────────────────────
@app.post("/api/video/analyze")
async def analyze_video(req: AnalyzeRequest):
    video_path = _find_video(req.video_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="Video not found")

    if req.mode == "equal_chunks":
        await broadcast({"type": "progress", "step": "analyzing", "pct": 20, "msg": f"Разбиваю всё видео на части по {req.chunk_minutes:g} мин..."})
        meta = await asyncio.to_thread(processor.extract_metadata, str(video_path))
        duration = float(meta.get("duration", 0))
        segments = generate_equal_chunks(duration, req.chunk_minutes)
        await broadcast({"type": "progress", "step": "done", "pct": 100, "msg": f"Видео разбито на {len(segments)} равных частей!"})
        return {"segments": segments}

    analyzer = _build_analyzer()

    async def on_progress(msg: str, pct: int):
        await broadcast({"type": "progress", "step": "analyzing", "pct": pct, "msg": msg})

    if req.mode == "transcript":
        unfinished = operation_manager.find_for_video(
            req.video_id,
            include_stopped=True,
        )
        if unfinished is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Whisper ещё не завершён. Продолжите или завершите "
                    "транскрипцию перед анализом."
                ),
            )
        # Читаем транскрипт или запускаем транскрипцию если её нет
        transcript_file = TRANSCRIPTS_DIR / f"{req.video_id}.json"
        if not transcript_file.exists():
            await broadcast({"type": "progress", "step": "transcribing", "pct": 5, "msg": "Создаю транскрипт (Whisper)..."})
            operation = transcription_service.start(
                req.video_id,
                str(video_path),
                None,
                transcript_file,
                asyncio.get_running_loop(),
            )
            operation = await transcription_service.wait_for_terminal(
                operation["operation_id"]
            )
            if operation["status"] != "completed" or not transcript_file.exists():
                detail = operation.get("error") or (
                    "Whisper был остановлен до завершения. Продолжите транскрипцию."
                )
                raise HTTPException(status_code=409, detail=detail)

        with open(transcript_file, "r", encoding="utf-8") as f:
            transcript_data = json.load(f)

        prompt = req.prompt.strip() or _DEFAULT_ANALYZE_PROMPT
        chapters_file = TRANSCRIPTS_DIR / f"{req.video_id}.chapters.json"
        if chapters_file.exists():
            with open(chapters_file, "r", encoding="utf-8") as chapter_input:
                chapter_data = json.load(chapter_input)
            chapter_lines = [
                f"[{item.get('start', 0):.1f} сек] {item.get('text', '')}"
                for item in chapter_data.get("segments", [])
                if item.get("text")
            ]
            if chapter_lines:
                prompt += (
                    "\n\nДополнительная карта тем видео из SRT-оглавления. "
                    "Используй её только для ориентации; точные границы бери из речевого транскрипта:\n"
                    + "\n".join(chapter_lines[:100])
                )
        segments = await analyzer.analyze_from_transcript(transcript_data, prompt, on_progress)
        # Подтягиваем границы к паузам в речи, чтобы клип не обрывался на полуслове
        segments = snap_segments_to_speech(
            segments, transcript_data, transcript_data.get("duration", 0)
        )
        _attach_transcript_text(segments, transcript_data.get("words", []))
    else:
        # Анализ по кадрам
        segments = await analyzer.analyze(str(video_path), req.prompt, on_progress)

    await broadcast({"type": "progress", "step": "done", "pct": 100, "msg": "Анализ завершён!"})
    return {"segments": segments}

# ─────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────
def _legacy_extract_words_from_json(t_data: dict) -> list[dict]:
    """Extract flat word list from transcript JSON (supports both formats)."""
    if not t_data:
        return []
    words = t_data.get("words", [])
    if words:
        return words

    segments = t_data.get("segments", [])
    flat_words = []
    for seg in segments:
        seg_words = seg.get("words", [])
        if seg_words:
            flat_words.extend(seg_words)
        else:
            text = seg.get("text", "").strip()
            s_start = float(seg.get("start", 0))
            s_end = float(seg.get("end", 0))
            if text and s_end > s_start:
                text_words = [w for w in text.split() if w]
                seg_dur = max(0.4, s_end - s_start)
                w_step = seg_dur / max(1, len(text_words))
                for idx, w_str in enumerate(text_words):
                    flat_words.append({
                        "word": w_str,
                        "start": round(s_start + idx * w_step, 3),
                        "end": round(s_start + (idx + 1) * w_step, 3),
                    })
    return flat_words


async def _export_segments(
    video_id: str,
    video_path: str,
    segments: list[dict],
    mode: str,
    padding: float,
    aspect_ratio: str,
    burn_subtitles: bool,
    insert_ad: bool,
    words: list[dict],
    text_ad_enabled: bool = False,
    text_ad_text: str = "",
    text_ad_position: str = "top",
    text_ad_interval: float = 4.0,
    text_ad_opacity: float = 0.75,
    video_banner_enabled: bool = False,
    video_banner_height: int = 380,
    base_pct: int = 10,
    span_pct: int = 90,
) -> list[dict]:
    """Общая нарезка клипов — используется и ручным экспортом, и авто-конвейером."""
    composition_needs_ad = any(
        (segment.get("composition") or {}).get("ad_layer", {}).get("enabled", True)
        for segment in segments
        if (segment.get("composition") or {}).get("ad_layer")
    )
    composition_needs_banner = any(
        (segment.get("composition") or {}).get("banner_layer", {}).get("enabled", True)
        for segment in segments
        if (segment.get("composition") or {}).get("banner_layer")
    )
    current_ad = _current_uploaded_video(ADS_DIR, "current_ad") if insert_ad or composition_needs_ad else None
    current_banner = _current_uploaded_video(BANNER_DIR, "current_banner") if video_banner_enabled or composition_needs_banner else None
    ad_path = str(current_ad) if current_ad else None
    banner_path = str(current_banner) if current_banner else None

    results: list[dict] = []
    export_id = uuid.uuid4().hex[:8]

    if mode == "concat":
        out_name = f"{video_id}_reel_{export_id}_{aspect_ratio.replace(':', '')}.mp4"
        out_path = str(CLIPS_DIR / out_name)
        await asyncio.to_thread(
            processor.export_concatenated,
            video_path, segments, out_path, padding, aspect_ratio, burn_subtitles, words,
            text_ad_enabled, text_ad_text, text_ad_position, text_ad_interval, text_ad_opacity,
            video_banner_enabled, banner_path or "", video_banner_height,
            overlay_ad_path=ad_path or "",
            insert_ad_path=ad_path if insert_ad else "",
        )
        await broadcast({"type": "progress", "step": "exporting",
                         "pct": base_pct + span_pct, "msg": "Готово!"})
        return [{"url": f"/clips/{out_name}", "label": f"Highlights Reel ({aspect_ratio})"}]

    total = len(segments)
    completed = 0
    progress_lock = asyncio.Lock()
    encoder_slots = asyncio.Semaphore(2)

    async def export_one(i: int, seg: dict):
        nonlocal completed
        suffix = "_shorts.mp4" if aspect_ratio == "9:16" else ".mp4"
        out_name = f"{video_id}_clip_{export_id}_{i + 1}{suffix}"
        out_path = str(CLIPS_DIR / out_name)
        try:
            async with encoder_slots:
                await asyncio.to_thread(
                    processor.clip_video,
                    video_path, seg["start"], seg["end"], out_path,
                    padding, aspect_ratio, "", burn_subtitles, words,
                    text_ad_enabled, text_ad_text, text_ad_position, text_ad_interval, text_ad_opacity,
                    video_banner_enabled, banner_path or "", video_banner_height,
                    composition=seg.get("composition"),
                    overlay_ad_path=ad_path or "",
                )
        except Exception as exc:
            logger.warning(f"Клип {i + 1} не удалось нарезать: {exc}")
            return i, None

        composition = seg.get("composition")
        ad_layer = composition.get("ad_layer") if composition else None
        should_insert_ad = bool(
            ad_path and (
                (composition is None and insert_ad)
                or (ad_layer and ad_layer.get("enabled", True) and ad_layer.get("mode") == "insert")
            )
        )
        if should_insert_ad and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            ad_out = out_path.replace(".mp4", "_ad.mp4")
            try:
                await broadcast({"type": "progress", "step": "exporting",
                                 "pct": base_pct + int((i + 0.5) / total * span_pct),
                                 "msg": f"📺 Вставляю рекламу в клип {i + 1}/{total}..."})
                await asyncio.to_thread(processor.insert_ad_into_clip, out_path, ad_path, ad_out)
                if os.path.exists(ad_out) and os.path.getsize(ad_out) > 0:
                    os.replace(ad_out, out_path)
                else:
                    Path(ad_out).unlink(missing_ok=True)
            except Exception as ad_err:
                logger.warning(f"Не удалось вставить рекламу в клип {i + 1}: {ad_err}")
                Path(ad_out).unlink(missing_ok=True)

        async with progress_lock:
            completed += 1
            pct = base_pct + int(completed / total * span_pct)
            await broadcast({"type": "progress", "step": "exporting", "pct": pct,
                             "msg": f"Клип {completed}/{total} готов"})

        return i, {
            "url": f"/clips/{out_name}",
            "label": seg.get("label", f"Клип {i + 1}"),
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "transcript_text": seg.get("transcript_text", ""),
            "segment_index": seg.get("segment_index", i),
        }

    exported = await asyncio.gather(*(export_one(i, seg) for i, seg in enumerate(segments)))
    results.extend(item for _, item in sorted(exported, key=lambda pair: pair[0]) if item)

    if not results:
        raise HTTPException(status_code=500, detail="Ни один клип не удалось нарезать. Проверьте, что FFmpeg установлен.")

    return results

@app.post("/api/video/export")
async def export_clips(req: ExportRequest):
    video_path = _find_video(req.video_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="Video not found")
    if not req.segments:
        raise HTTPException(status_code=400, detail="Не выбраны фрагменты для экспорта")
    segments = [segment.model_dump(exclude_none=True) for segment in req.segments]
    if req.mode not in {"separate", "concat"}:
        raise HTTPException(status_code=400, detail="Неизвестный режим экспорта")
    if req.aspect_ratio not in {"original", "9:16"}:
        raise HTTPException(status_code=400, detail="Неизвестный формат кадра")
    source_meta = await asyncio.to_thread(processor.extract_metadata, str(video_path))
    source_duration = float(source_meta.get("duration") or 0)
    segments = [validate_composition_for_source(segment, source_duration) for segment in segments]
    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Некорректные таймкоды фрагмента")

    words = []
    transcript_file = TRANSCRIPTS_DIR / f"{req.video_id}.json"
    if transcript_file.exists():
        try:
            with open(transcript_file, "r", encoding="utf-8") as f:
                t_data = json.load(f)
            words = _extract_words_from_json(t_data)
        except Exception:
            pass

    await broadcast({"type": "progress", "step": "exporting", "pct": 10, "msg": "Нарезаю клипы..."})

    results = await _export_segments(
        video_id=req.video_id,
        video_path=str(video_path),
        segments=segments,
        mode=req.mode,
        padding=req.padding,
        aspect_ratio=req.aspect_ratio,
        burn_subtitles=req.burn_subtitles,
        insert_ad=req.insert_ad,
        words=words,
        text_ad_enabled=req.text_ad_enabled,
        text_ad_text=req.text_ad_text,
        text_ad_position=req.text_ad_position,
        text_ad_interval=req.text_ad_interval,
        text_ad_opacity=req.text_ad_opacity,
        video_banner_enabled=req.video_banner_enabled,
        video_banner_height=req.video_banner_height,
    )
    return {"clips": results}


def _current_uploaded_video(directory: Path, prefix: str) -> Path | None:
    for ext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        candidate = directory / f"{prefix}{ext}"
        if candidate.is_file():
            return candidate
    return None


def validate_composition_for_source(segment: dict, source_duration: float) -> dict:
    """Validate source-dependent composition rules before creating output files."""
    composition = segment.get("composition")
    if not composition:
        return segment
    try:
        start = float(composition["source_start"])
        end = float(composition["source_end"])
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise ValueError("Не удалось определить длительность исходного видео")
        if start < 0 or end <= start or end > source_duration + 0.001:
            raise ValueError("Границы клипа выходят за пределы исходного видео")
        if abs(float(segment["start"]) - start) > 0.001 or abs(float(segment["end"]) - end) > 0.001:
            raise ValueError("Границы клипа не совпадают с настройками композиции")
        banner = composition.get("banner_layer")
        if banner and banner.get("enabled", True) and _current_uploaded_video(BANNER_DIR, "current_banner") is None:
            raise ValueError("Видео-баннер не найден — загрузите его заново")
        ad = composition.get("ad_layer")
        if ad and ad.get("enabled", True) and _current_uploaded_video(ADS_DIR, "current_ad") is None:
            raise ValueError("Видео-реклама не найдена — загрузите её заново")
    except (KeyError, TypeError, ValueError) as exc:
        detail = str(exc) or "Некорректные настройки композиции клипа"
        raise HTTPException(status_code=422, detail=detail) from exc
    return segment

# ─────────────────────────────────────────────
# Ad Management
# ─────────────────────────────────────────────
@app.post("/api/ad/upload")
async def upload_ad(file: UploadFile = File(...)):
    """Upload an ad video. Only one ad is stored at a time (overwrites previous)."""
    ext = Path(file.filename or "").suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mkv", ".mov", ".webm", ".avi"}:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат видео для рекламы")

    # Remove any existing ad files
    for existing in ADS_DIR.glob("current_ad.*"):
        existing.unlink(missing_ok=True)

    ad_path = ADS_DIR / f"current_ad{ext}"
    try:
        async with aiofiles.open(ad_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                await f.write(chunk)
        if ad_path.stat().st_size == 0:
            raise ValueError("Загружен пустой файл")

        meta = await asyncio.to_thread(processor.extract_metadata, str(ad_path))
        return {
            "success": True,
            "filename": file.filename,
            "duration": meta.get("duration", 0),
            "width": meta.get("width", 0),
            "height": meta.get("height", 0),
            "size_bytes": meta.get("size_bytes", 0),
            "url": f"/ads/{ad_path.name}",
        }
    except Exception as exc:
        ad_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Ошибка загрузки рекламы: {exc}") from exc
    finally:
        await file.close()


@app.get("/api/ad/current")
async def get_current_ad():
    """Get info about the currently uploaded ad."""
    for ext in [".mp4", ".mkv", ".mov", ".webm", ".avi"]:
        ad_path = ADS_DIR / f"current_ad{ext}"
        if ad_path.exists():
            try:
                meta = await asyncio.to_thread(processor.extract_metadata, str(ad_path))
                return {
                    "has_ad": True,
                    "filename": f"current_ad{ext}",
                    "duration": meta.get("duration", 0),
                    "width": meta.get("width", 0),
                    "height": meta.get("height", 0),
                    "size_bytes": meta.get("size_bytes", 0),
                    "url": f"/ads/current_ad{ext}",
                }
            except Exception:
                return {"has_ad": True, "filename": f"current_ad{ext}", "url": f"/ads/current_ad{ext}"}
    return {"has_ad": False}


@app.delete("/api/ad/remove")
async def remove_ad():
    """Remove the current ad."""
    for existing in ADS_DIR.glob("current_ad.*"):
        existing.unlink(missing_ok=True)
    return {"success": True}


app.mount("/ads", StaticFiles(directory=str(ADS_DIR)), name="ads")
app.mount("/banners", StaticFiles(directory=str(BANNER_DIR)), name="banners")


# ─────────────────────────────────────────────
# Video Banner Management
# ─────────────────────────────────────────────
@app.post("/api/banner/upload")
async def upload_banner(file: UploadFile = File(...)):
    """Загрузить видео-баннер (зацикленное мини-видео в верхней части клипа)."""
    ext = Path(file.filename or "").suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mkv", ".mov", ".webm", ".avi"}:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат видео для баннера")

    for existing in BANNER_DIR.glob("current_banner.*"):
        existing.unlink(missing_ok=True)

    banner_path = BANNER_DIR / f"current_banner{ext}"
    try:
        async with aiofiles.open(banner_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                await f.write(chunk)
        if banner_path.stat().st_size == 0:
            raise ValueError("Загружен пустой файл")

        meta = await asyncio.to_thread(processor.extract_metadata, str(banner_path))
        return {
            "success": True,
            "filename": file.filename,
            "duration": meta.get("duration", 0),
            "width": meta.get("width", 0),
            "height": meta.get("height", 0),
            "size_bytes": meta.get("size_bytes", 0),
            "url": f"/banners/{banner_path.name}",
        }
    except Exception as exc:
        banner_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Ошибка загрузки баннера: {exc}") from exc
    finally:
        await file.close()


@app.get("/api/banner/current")
async def get_current_banner():
    """Получить инфо о текущем баннере."""
    for ext in [".mp4", ".mkv", ".mov", ".webm", ".avi"]:
        bp = BANNER_DIR / f"current_banner{ext}"
        if bp.exists():
            try:
                meta = await asyncio.to_thread(processor.extract_metadata, str(bp))
                return {
                    "has_banner": True,
                    "filename": f"current_banner{ext}",
                    "duration": meta.get("duration", 0),
                    "width": meta.get("width", 0),
                    "height": meta.get("height", 0),
                    "size_bytes": meta.get("size_bytes", 0),
                    "url": f"/banners/current_banner{ext}",
                }
            except Exception:
                return {"has_banner": True, "filename": f"current_banner{ext}", "url": f"/banners/current_banner{ext}"}
    return {"has_banner": False}


@app.delete("/api/banner/remove")
async def remove_banner():
    """Удалить текущий баннер."""
    for existing in BANNER_DIR.glob("current_banner.*"):
        existing.unlink(missing_ok=True)
    return {"success": True}



# ─────────────────────────────────────────────
# Social Media
# ─────────────────────────────────────────────
@app.get("/api/social/status")
async def social_status():
    """Статус подключения соцсетей."""
    return {
        "youtube": {
            "configured": youtube_uploader.is_configured,
            "authenticated": youtube_uploader.is_authenticated,
            "name": "YouTube",
            "mode": "auto" if youtube_uploader.is_authenticated else "semi",
            "supports_semi_auto": True,
        },
        # Instagram и TikTok закрыли загрузку через API для личных аккаунтов,
        # поэтому они всегда работают в полуавтоматическом режиме.
        "instagram": {
            "configured": True,
            "authenticated": False,
            "name": "Instagram",
            "mode": "semi",
            "supports_semi_auto": True,
        },
        "tiktok": {
            "configured": True,
            "authenticated": False,
            "name": "TikTok",
            "mode": "semi",
            "supports_semi_auto": True,
        },
    }


@app.get("/api/social/youtube/auth")
async def youtube_auth():
    """Start YouTube OAuth2 flow. Returns the auth URL to redirect to."""
    auth_url = youtube_uploader.get_auth_url()
    if not auth_url:
        raise HTTPException(
            status_code=400,
            detail="YouTube API не настроен. Поместите client_secret.json в папку backend/.",
        )
    return {"auth_url": auth_url}


@app.get("/api/social/youtube/callback")
async def youtube_callback(code: str = ""):
    """Handle YouTube OAuth2 callback."""
    if not code:
        raise HTTPException(status_code=400, detail="Не получен authorization code")
    success = youtube_uploader.handle_callback(code)
    if success:
        return JSONResponse(content="""
        <html><body style="background:#0a0e1a;color:#fff;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center">
        <div><h1>✅ YouTube подключён!</h1><p>Можете закрыть это окно и вернуться в приложение.</p></div>
        </body></html>
        """, media_type="text/html")
    raise HTTPException(status_code=400, detail="Не удалось авторизоваться на YouTube")


@app.delete("/api/social/youtube/disconnect")
async def youtube_disconnect():
    """Disconnect YouTube account."""
    youtube_uploader.disconnect()
    return {"success": True}


@app.post("/api/social/upload")
async def social_upload(req: SocialUploadRequest):
    """Загружает клип на платформу (полностью автоматически — только YouTube)."""
    clip_path = _resolve_clip_path(req.clip_url)
    if not clip_path:
        raise HTTPException(status_code=404, detail="Клип не найден на сервере")

    try:
        if req.platform == "youtube":
            return await asyncio.to_thread(
                youtube_uploader.upload,
                clip_path, req.title, req.description, req.tags, req.privacy,
            )
        if req.platform in ("instagram", "tiktok"):
            raise HTTPException(
                status_code=400,
                detail=f"{req.platform.title()} не поддерживает автоматическую загрузку без бизнес-аккаунта. "
                       f"Используйте полуавтоматический режим — кнопка «Подготовить и открыть».",
            )
        raise HTTPException(status_code=400, detail=f"Неизвестная платформа: {req.platform}")
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки: {str(e)}")


# ─────────────────────────────────────────────
# Полуавтоматическая публикация
# ─────────────────────────────────────────────
_UPLOAD_PAGES = {
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/tiktokstudio/upload",
    "youtube": "https://studio.youtube.com/channel/UC/videos/upload",
    "shorts": "https://www.youtube.com/upload",
    "vk": "https://vk.com/clips",
    "telegram": "https://web.telegram.org/",
}

_PLATFORM_LIMITS = {
    "instagram": {"title": 125, "description": 2200, "max_tags": 20},
    "tiktok": {"title": 100, "description": 2200, "max_tags": 10},
    "youtube": {"title": 100, "description": 5000, "max_tags": 15},
}

_STOPWORDS = {
    "это", "как", "что", "так", "вот", "если", "чтобы", "когда", "потому", "очень",
    "меня", "тебя", "себя", "него", "нибудь", "может", "быть", "было", "были", "есть",
    "вообще", "просто", "здесь", "тогда", "после", "перед", "через", "около", "надо",
    "нужно", "будет", "будем", "такой", "такая", "такие", "который", "которая",
    "the", "and", "for", "you", "this", "that", "with", "have", "just", "about",
}


def _resolve_clip_path(clip_url: str) -> Optional[str]:
    """Безопасно превращает URL клипа в путь на диске, не выпуская за пределы папки клипов."""
    name = Path(urllib.parse.unquote(clip_url or "")).name
    if not name:
        return None
    candidate = (CLIPS_DIR / name).resolve()
    try:
        candidate.relative_to(CLIPS_DIR.resolve())
    except ValueError:
        return None
    return str(candidate) if candidate.exists() else None


def _fallback_hashtags(text: str, label: str, platform: str) -> list[str]:
    """Хэштеги из самых частых значимых слов — работает без ИИ."""
    base = {
        "instagram": ["reels", "рекомендации", "рек"],
        "tiktok": ["fyp", "рекомендации", "втоп"],
        "youtube": ["shorts", "шортс"],
    }.get(platform, ["shorts"])

    words = re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", f"{label} {text}".lower())
    freq: dict[str, int] = {}
    for w in words:
        if w in _STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1

    top = [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:7]]
    return base + top


@app.post("/api/social/prepare")
async def social_prepare(req: SocialPrepareRequest):
    """
    Полуавтоматическая публикация: готовит заголовок, описание и хэштеги,
    отдаёт прямую ссылку на файл и адрес страницы загрузки.

    Instagram и TikTok не дают загружать видео через API без бизнес-аккаунта
    и модерации приложения, поэтому финальную кнопку «Опубликовать» нажимает
    пользователь — всё остальное готовится автоматически.
    """
    platform = (req.platform or "").lower()
    if platform not in _UPLOAD_PAGES:
        raise HTTPException(status_code=400, detail=f"Неизвестная платформа: {req.platform}")

    clip_path = _resolve_clip_path(req.clip_url)
    if not clip_path:
        raise HTTPException(status_code=404, detail="Клип не найден на сервере")

    limits = _PLATFORM_LIMITS.get(platform, _PLATFORM_LIMITS["youtube"])
    source_text = (req.transcript_text or "").strip()
    label = (req.label or "").strip() or "Новый клип"

    title, description, hashtags = "", "", []

    # Пытаемся сгенерировать через ИИ, если ключ введён
    if settings_store.get("api_key") and source_text:
        try:
            analyzer = _build_analyzer()
            generated = await analyzer.generate_social_copy(
                text=source_text[:4000], label=label, platform=platform, limits=limits,
            )
            title = (generated.get("title") or "").strip()
            description = (generated.get("description") or "").strip()
            hashtags = [h.strip().lstrip("#") for h in generated.get("hashtags", []) if h.strip()]
        except Exception as exc:
            logger.warning(f"ИИ не смог сгенерировать описание, беру запасной вариант: {exc}")

    # Запасной вариант без ИИ
    if not title:
        title = label[: limits["title"]]
    if not hashtags:
        hashtags = _fallback_hashtags(source_text, label, platform)
    if not description:
        snippet = source_text[:300].rsplit(" ", 1)[0] if len(source_text) > 300 else source_text
        description = snippet or label

    hashtags = list(dict.fromkeys(h for h in hashtags if h))[: limits["max_tags"]]
    tag_line = " ".join(f"#{h}" for h in hashtags)
    full_description = f"{description}\n\n{tag_line}".strip()[: limits["description"]]

    return {
        "platform": platform,
        "title": title[: limits["title"]],
        "description": full_description,
        "hashtags": hashtags,
        "upload_url": _UPLOAD_PAGES[platform],
        "clip_url": req.clip_url,
        "download_url": f"/clips/{Path(clip_path).name}",
        "file_name": Path(clip_path).name,
        "file_size_mb": round(os.path.getsize(clip_path) / 1024 / 1024, 1),
        "instructions": _SEMI_AUTO_STEPS.get(platform, []),
    }


_SEMI_AUTO_STEPS = {
    "instagram": [
        "Клип сохранён — нажмите «Скачать клип», если он ещё не на устройстве.",
        "Откроется Instagram. Нажмите «+» → «Reels» и выберите скачанный файл.",
        "Описание с хэштегами уже скопировано — вставьте его в поле подписи (Ctrl+V).",
        "Нажмите «Поделиться».",
    ],
    "tiktok": [
        "Клип сохранён — нажмите «Скачать клип», если он ещё не на устройстве.",
        "Откроется TikTok Studio. Перетащите файл в окно загрузки.",
        "Описание с хэштегами уже скопировано — вставьте его (Ctrl+V).",
        "Нажмите «Опубликовать».",
    ],
    "youtube": [
        "Клип сохранён на диск.",
        "Откроется YouTube Studio. Перетащите файл в окно загрузки.",
        "Заголовок и описание скопированы — вставьте их в нужные поля.",
        "Выберите «Shorts» и нажмите «Опубликовать».",
    ],
}


@app.get("/api/clips/download/{filename}")
async def download_clip(filename: str):
    """Отдаёт клип как файл для скачивания с корректным именем."""
    clip_path = _resolve_clip_path(filename)
    if not clip_path:
        raise HTTPException(status_code=404, detail="Клип не найден")
    return FileResponse(clip_path, media_type="video/mp4", filename=Path(clip_path).name)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _find_video(video_id: str) -> Optional[str]:
    try:
        normalized_id = str(uuid.UUID(video_id))
    except ValueError:
        return None
    for ext in [".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"]:
        p = UPLOAD_DIR / f"{normalized_id}{ext}"
        if p.exists():
            return str(p)
    return None
