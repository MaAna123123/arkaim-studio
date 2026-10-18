"""Safe, atomic on-disk storage for persistent video projects."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable
from urllib.parse import urlsplit


SCHEMA_VERSION = 1

SUPPORTED_EDITOR_FIELDS = frozenset({
    "segments", "selected", "clip_results", "subtitle_context", "subtitle_notice",
    "export_padding", "aspect_ratio", "burn_subtitles", "insert_ad",
    "text_ad_enabled", "text_ad_text", "text_ad_position", "text_ad_interval",
    "text_ad_opacity", "video_banner_enabled", "video_banner_height",
})

DEFAULT_EDITOR = {
    "segments": [],
    "selected": [],
    "clip_results": [],
    "subtitle_context": None,
    "subtitle_notice": "",
    "export_padding": 0.5,
    "aspect_ratio": "9:16",
    "burn_subtitles": True,
    "insert_ad": False,
    "text_ad_enabled": False,
    "text_ad_text": "",
    "text_ad_position": "top",
    "text_ad_interval": 4,
    "text_ad_opacity": 0.75,
    "video_banner_enabled": False,
    "video_banner_height": 380,
}

_VIDEO_SCALAR_FIELDS = frozenset({
    "duration", "fps", "width", "height", "size_bytes", "codec", "thumbnail",
    "uploader", "view_count", "description", "has_chapters", "platform",
    "has_subtitles", "subtitle_lang", "subtitle_is_auto",
})
_CHAPTER_FIELDS = frozenset({"start_time", "end_time", "title", "index"})
_EDITOR_LIST_FIELDS = frozenset({"segments", "selected", "clip_results"})
_EDITOR_STRING_FIELDS = frozenset({
    "subtitle_notice", "aspect_ratio", "text_ad_text", "text_ad_position",
})
_EDITOR_BOOLEAN_FIELDS = frozenset({
    "burn_subtitles", "insert_ad", "text_ad_enabled", "video_banner_enabled",
})
_EDITOR_NUMBER_FIELDS = frozenset({
    "export_padding", "text_ad_interval", "text_ad_opacity", "video_banner_height",
})
_SENSITIVE_EDITOR_KEY_PARTS = (
    "api_key", "token", "secret", "password", "credential", "subscription",
    "oauth", "private_key",
)
_UNSAFE_EDITOR_VALUE = object()


class UnsupportedProjectSchemaError(ValueError):
    """A project record was written by a newer incompatible version."""


class InvalidProjectIdentityError(ValueError):
    """A project record is not bound to the directory selected by its UUID."""


class ProjectStore:
    """Owns all local filesystem access for version-one project records."""

    def __init__(self, project_root: str | Path, legacy_transcripts_dir: str | Path | None = None):
        self.project_root = Path(project_root).resolve()
        self.projects_root = self.project_root / "data" / "projects"
        self.legacy_transcripts_dir = (
            Path(legacy_transcripts_dir).resolve() if legacy_transcripts_dir else None
        )
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def _validate_id(project_id: str) -> str:
        """Return a canonical UUID, rejecting strings before they reach the filesystem."""
        try:
            return str(uuid.UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("project_id must be a UUID") from exc

    def _project_dir(self, project_id: str) -> Path:
        normalized_id = self._validate_id(project_id)
        directory = self.projects_root / normalized_id
        try:
            directory.resolve().relative_to(self.projects_root.resolve())
        except ValueError as exc:
            raise ValueError("project path must stay below data/projects") from exc
        return directory

    def _project_lock(self, project_id: str) -> threading.RLock:
        normalized_id = self._validate_id(project_id)
        with self._locks_guard:
            return self._locks.setdefault(normalized_id, threading.RLock())

    def _destination_project_id(self, destination: str | Path) -> str:
        path = Path(destination)
        try:
            relative = path.resolve().relative_to(self.projects_root.resolve())
        except ValueError as exc:
            raise ValueError("destination must stay below data/projects") from exc
        if len(relative.parts) != 2:
            raise ValueError("destination must be a project file")
        return self._validate_id(relative.parts[0])

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _relative_source_path(self, source_path: str | Path) -> str:
        raw_path = Path(source_path)
        if not str(source_path):
            raise ValueError("source_path is required")
        candidate = raw_path.resolve() if raw_path.is_absolute() else (self.project_root / raw_path).resolve()
        try:
            return candidate.relative_to(self.project_root).as_posix()
        except ValueError as exc:
            raise ValueError("source_path must stay below the project root") from exc

    @staticmethod
    def _safe_scalar(value: Any) -> bool:
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            return False
        if isinstance(value, str):
            path_like = PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
            remote_url = value.casefold().startswith(("http://", "https://"))
            if path_like or remote_url:
                return False
        return True

    @staticmethod
    def _safe_thumbnail_url(value: Any) -> bool:
        """Accept only an unauthenticated, non-tokenized image URL for thumbnails."""
        if not isinstance(value, str):
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            return False
        return Path(parsed.path).suffix.casefold() in {".gif", ".jpeg", ".jpg", ".png", ".webp"}

    def _safe_video_value(self, field: str, value: Any) -> bool:
        if field == "thumbnail" and isinstance(value, str) and value.casefold().startswith(("http://", "https://")):
            return self._safe_thumbnail_url(value)
        return self._safe_scalar(value)

    @staticmethod
    def _source_filename(value: str) -> str:
        """Keep display filenames from becoming an alternate path disclosure."""
        filename = PureWindowsPath(str(value)).name
        return PurePosixPath(filename).name

    def _safe_video_metadata(self, video: Any) -> dict[str, Any]:
        if not isinstance(video, dict):
            return {}
        safe: dict[str, Any] = {}
        for field in _VIDEO_SCALAR_FIELDS:
            value = video.get(field)
            if field in video and self._safe_video_value(field, value):
                safe[field] = value
        chapters = video.get("chapters")
        if isinstance(chapters, list):
            safe_chapters = []
            for chapter in chapters:
                if not isinstance(chapter, dict):
                    continue
                safe_chapter = {
                    field: value
                    for field, value in chapter.items()
                    if field in _CHAPTER_FIELDS and self._safe_scalar(value)
                }
                if safe_chapter:
                    safe_chapters.append(safe_chapter)
            safe["chapters"] = safe_chapters
        return safe

    @staticmethod
    def _is_sensitive_editor_key(key: Any) -> bool:
        if not isinstance(key, str):
            return True
        normalized = key.casefold()
        return any(part in normalized for part in _SENSITIVE_EDITOR_KEY_PARTS)

    @staticmethod
    def _is_safe_editor_string(value: str) -> bool:
        if PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute():
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if parsed.scheme in {"http", "https"}:
            return not (
                parsed.username or parsed.password or parsed.query or parsed.fragment
                or "subscription" in parsed.path.casefold()
            )
        return True

    def _sanitize_editor_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return copy.deepcopy(value)
        if isinstance(value, str):
            return value if self._is_safe_editor_string(value) else _UNSAFE_EDITOR_VALUE
        if isinstance(value, list):
            safe_items = []
            for item in value:
                sanitized = self._sanitize_editor_value(item)
                if sanitized is not _UNSAFE_EDITOR_VALUE:
                    safe_items.append(sanitized)
            return safe_items
        if isinstance(value, dict):
            safe_mapping = {}
            for key, item in value.items():
                if self._is_sensitive_editor_key(key):
                    continue
                sanitized = self._sanitize_editor_value(item)
                if sanitized is not _UNSAFE_EDITOR_VALUE:
                    safe_mapping[key] = sanitized
            return safe_mapping
        return _UNSAFE_EDITOR_VALUE

    def _sanitize_editor_field(self, field: str, value: Any) -> Any:
        if field in _EDITOR_LIST_FIELDS:
            return self._sanitize_editor_value(value) if isinstance(value, list) else _UNSAFE_EDITOR_VALUE
        if field == "subtitle_context":
            if value is None:
                return None
            return self._sanitize_editor_value(value) if isinstance(value, (dict, list)) else _UNSAFE_EDITOR_VALUE
        if field in _EDITOR_STRING_FIELDS:
            return self._sanitize_editor_value(value) if isinstance(value, str) else _UNSAFE_EDITOR_VALUE
        if field in _EDITOR_BOOLEAN_FIELDS:
            return value if isinstance(value, bool) else _UNSAFE_EDITOR_VALUE
        if field in _EDITOR_NUMBER_FIELDS:
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else _UNSAFE_EDITOR_VALUE
        return _UNSAFE_EDITOR_VALUE

    def _safe_editor_state(self, editor: Any) -> dict[str, Any]:
        values = copy.deepcopy(DEFAULT_EDITOR)
        if not isinstance(editor, dict):
            return values
        for field in SUPPORTED_EDITOR_FIELDS:
            if field not in editor:
                continue
            sanitized = self._sanitize_editor_field(field, editor[field])
            if sanitized is not _UNSAFE_EDITOR_VALUE:
                values[field] = sanitized
        return values

    def _atomic_write_json(self, destination: str | Path, data: Any) -> None:
        """Durably replace a project JSON file without risking the last valid copy."""
        destination = Path(destination)
        project_id = self._destination_project_id(destination)
        with self._project_lock(project_id):
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=destination.parent,
                    prefix=f".{destination.name}.tmp-",
                    delete=False,
                ) as temporary_file:
                    temporary_name = temporary_file.name
                    json.dump(data, temporary_file, ensure_ascii=False, indent=2)
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
                os.replace(temporary_name, destination)
            finally:
                if temporary_name:
                    Path(temporary_name).unlink(missing_ok=True)

    def _read_json(self, destination: str | Path) -> dict[str, Any]:
        destination = Path(destination)
        self._destination_project_id(destination)
        with destination.open("r", encoding="utf-8") as project_file:
            data = json.load(project_file)
        if not isinstance(data, dict):
            raise ValueError("project JSON must contain an object")
        return data

    def _validate_project_schema(
        self, project: dict[str, Any], expected_project_id: str | None = None
    ) -> None:
        version = project.get("schema_version")
        if not isinstance(version, int):
            raise ValueError("project schema_version must be an integer")
        if version > SCHEMA_VERSION:
            raise UnsupportedProjectSchemaError("unsupported project schema")
        if version != SCHEMA_VERSION:
            raise ValueError("unsupported project schema")
        if expected_project_id is not None:
            try:
                project_id = self._validate_id(project.get("project_id"))
                video_id = self._validate_id(project.get("video_id"))
            except ValueError as exc:
                raise InvalidProjectIdentityError("project identity is invalid") from exc
            if project_id != expected_project_id or video_id != expected_project_id:
                raise InvalidProjectIdentityError("project identity must match directory")

    def create_project(
        self,
        video_id: str,
        title: str,
        source_filename: str,
        source_path: str | Path,
        video: dict[str, Any],
    ) -> dict[str, Any]:
        project_id = self._validate_id(video_id)
        project_directory = self._project_dir(project_id)
        now = self._utc_now()
        project = {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "video_id": project_id,
            "title": str(title),
            "source_filename": self._source_filename(source_filename),
            "source_path": self._relative_source_path(source_path),
            "video": self._safe_video_metadata(video),
            "editor": copy.deepcopy(DEFAULT_EDITOR),
            "created_at": now,
            "updated_at": now,
        }
        with self._project_lock(project_id):
            self._atomic_write_json(project_directory / "project.json", project)
        return project

    def get_project(self, project_id: str) -> dict[str, Any]:
        normalized_id = self._validate_id(project_id)
        with self._project_lock(normalized_id):
            project = self._read_json(self._project_dir(normalized_id) / "project.json")
            self._validate_project_schema(project, normalized_id)

        source_path = project.get("source_path")
        try:
            project["source_path"] = self._relative_source_path(source_path)
        except (TypeError, ValueError):
            project.pop("source_path", None)
        project["video"] = self._safe_video_metadata(project.get("video"))
        project["editor"] = self._safe_editor_state(project.get("editor"))
        self.load_transcript(normalized_id)
        return project

    def update_editor(self, project_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Apply an editor-only patch without permitting project identity changes."""
        normalized_id = self._validate_id(project_id)
        if not isinstance(updates, dict):
            raise ValueError("editor updates must be an object")
        unknown_fields = set(updates) - SUPPORTED_EDITOR_FIELDS
        if unknown_fields:
            raise ValueError("unsupported editor field")

        destination = self._project_dir(normalized_id) / "project.json"
        with self._project_lock(normalized_id):
            project = self._read_json(destination)
            self._validate_project_schema(project, normalized_id)
            editor = self._safe_editor_state(project.get("editor"))
            for field, value in updates.items():
                sanitized = self._sanitize_editor_field(field, value)
                if sanitized is _UNSAFE_EDITOR_VALUE:
                    raise ValueError("invalid editor value")
                editor[field] = sanitized
            project["editor"] = editor
            project["updated_at"] = self._utc_now()
            self._atomic_write_json(destination, project)
        return self.get_project(normalized_id)

    def source_status(self, project_id: str) -> dict[str, Any]:
        """Report whether a stored source path still resolves to a regular file."""
        normalized_id = self._validate_id(project_id)
        with self._project_lock(normalized_id):
            project = self._read_json(self._project_dir(normalized_id) / "project.json")
            self._validate_project_schema(project, normalized_id)
        source_path = project.get("source_path")
        try:
            relative_path = self._relative_source_path(source_path)
            exists = (self.project_root / relative_path).is_file()
        except (TypeError, ValueError):
            exists = False
        return {
            "exists": exists,
            "diagnostic": None if exists else {
                "code": "source_missing",
                "message": "Исходный файл не найден",
            },
        }

    @staticmethod
    def _transcription_summary(checkpoint: Any) -> dict[str, Any] | None:
        if not isinstance(checkpoint, dict):
            return None
        partial_result = checkpoint.get("partial_result")
        if not isinstance(partial_result, dict):
            partial_result = {}
        return {
            "operation_id": checkpoint.get("operation_id"),
            "status": checkpoint.get("status"),
            "progress_pct": checkpoint.get("progress_pct"),
            "checkpoint": partial_result.get("checkpoint"),
        }

    def _is_recoverable_checkpoint(self, checkpoint: Any, project_id: str) -> bool:
        if not isinstance(checkpoint, dict):
            return False
        if checkpoint.get("schema_version") != SCHEMA_VERSION:
            return False
        try:
            self._validate_id(checkpoint.get("operation_id"))
            video_id = self._validate_id(checkpoint.get("video_id"))
        except ValueError:
            return False
        partial_result = checkpoint.get("partial_result")
        checkpoint_position = (
            partial_result.get("checkpoint") if isinstance(partial_result, dict) else None
        )
        return (
            video_id == project_id
            and checkpoint.get("operation_type") == "transcription"
            and isinstance(checkpoint.get("status"), str)
            and isinstance(checkpoint.get("progress_pct"), (int, float))
            and not isinstance(checkpoint.get("progress_pct"), bool)
            and isinstance(checkpoint_position, (int, float))
            and not isinstance(checkpoint_position, bool)
        )

    def _project_summary(
        self, project: dict[str, Any], project_directory: Path
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        project_id = self._validate_id(project.get("project_id"))
        checkpoint_path = project_directory / "checkpoint.json"
        checkpoint = None
        checkpoint_diagnostic = None
        if checkpoint_path.is_file():
            try:
                checkpoint = self._read_json(checkpoint_path)
                if not self._is_recoverable_checkpoint(checkpoint, project_id):
                    checkpoint_diagnostic = {
                        "code": "checkpoint_invalid",
                        "project_id": project_id,
                        "message": "Сохранённый прогресс нельзя продолжить",
                    }
            except (OSError, ValueError, json.JSONDecodeError):
                checkpoint_diagnostic = {
                    "code": "checkpoint_unreadable",
                    "project_id": project_id,
                    "message": "Не удалось прочитать сохранённый прогресс",
                }
        source_path = project.get("source_path")
        try:
            relative_path = self._relative_source_path(source_path)
            source_exists = (self.project_root / relative_path).is_file()
        except (TypeError, ValueError):
            source_exists = False
        editor = project.get("editor") if isinstance(project.get("editor"), dict) else {}
        clip_results = editor.get("clip_results")
        video = project.get("video") if isinstance(project.get("video"), dict) else {}
        summary = {
            "project_id": project_id,
            "video_id": project.get("video_id"),
            "title": project.get("title"),
            "filename": self._source_filename(project.get("source_filename", "")),
            "updated_at": project.get("updated_at"),
            "duration": video.get("duration"),
            "source_exists": source_exists,
            "has_transcript": (project_directory / "transcript.json").is_file(),
            "clips_count": len(clip_results) if isinstance(clip_results, list) else 0,
            "transcription": (
                {
                    "resume_available": False,
                    "diagnostic": {
                        "code": checkpoint_diagnostic["code"],
                        "message": checkpoint_diagnostic["message"],
                    },
                }
                if checkpoint_diagnostic else self._transcription_summary(checkpoint)
            ),
        }
        return summary, checkpoint_diagnostic

    def list_projects(self, limit: int = 20) -> dict[str, Any]:
        """Return bounded recent-project summaries without changing on-disk retention."""
        try:
            response_limit = int(limit)
        except (TypeError, ValueError):
            response_limit = 20
        response_limit = max(1, min(response_limit, 20))
        if not self.projects_root.is_dir():
            return {"projects": [], "diagnostics": []}

        projects = []
        diagnostics = []
        for project_directory in self.projects_root.iterdir():
            if not project_directory.is_dir():
                continue
            try:
                project_id = self._validate_id(project_directory.name)
                with self._project_lock(project_id):
                    project = self._read_json(project_directory / "project.json")
                    self._validate_project_schema(project, project_id)
                summary, checkpoint_diagnostic = self._project_summary(project, project_directory)
                projects.append(summary)
                if checkpoint_diagnostic:
                    diagnostics.append(checkpoint_diagnostic)
            except UnsupportedProjectSchemaError:
                diagnostics.append({
                    "code": "project_schema_unsupported",
                    "project_id": project_directory.name,
                    "message": "Неподдерживаемая версия проекта",
                })
            except InvalidProjectIdentityError:
                diagnostics.append({
                    "code": "project_identity_invalid",
                    "project_id": project_directory.name,
                    "message": "Недопустимая идентичность проекта",
                })
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                diagnostics.append({
                    "code": "project_unreadable",
                    "project_id": project_directory.name,
                    "message": (
                        "invalid project JSON"
                        if isinstance(exc, json.JSONDecodeError)
                        else "invalid project record"
                    ),
                })
        projects.sort(key=lambda project: str(project.get("updated_at") or ""), reverse=True)
        return {"projects": projects[:response_limit], "diagnostics": diagnostics}

    def _require_project(self, project_id: str) -> tuple[str, Path]:
        normalized_id = self._validate_id(project_id)
        project_directory = self._project_dir(normalized_id)
        project = self._read_json(project_directory / "project.json")
        self._validate_project_schema(project, normalized_id)
        return normalized_id, project_directory

    def save_checkpoint(
        self,
        project_id: str,
        checkpoint: dict[str, Any],
        *,
        is_current: Callable[[], bool] | None = None,
    ) -> dict[str, Any] | None:
        """Persist a checkpoint only while its caller still owns the project."""
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be an object")
        normalized_id = self._validate_id(project_id)
        with self._project_lock(normalized_id):
            _, project_directory = self._require_project(normalized_id)
            saved = copy.deepcopy(checkpoint)
            if is_current is not None and not is_current():
                return None
            self._atomic_write_json(project_directory / "checkpoint.json", saved)
        return saved

    def load_checkpoint(self, project_id: str) -> dict[str, Any] | None:
        """Load a saved snapshot, returning None only when no checkpoint exists."""
        normalized_id = self._validate_id(project_id)
        with self._project_lock(normalized_id):
            _, project_directory = self._require_project(normalized_id)
            destination = project_directory / "checkpoint.json"
            if not destination.is_file():
                return None
            return self._read_json(destination)

    def remove_checkpoint(self, project_id: str) -> bool:
        """Remove an existing incomplete snapshot after final output is secured."""
        normalized_id = self._validate_id(project_id)
        with self._project_lock(normalized_id):
            _, project_directory = self._require_project(normalized_id)
            destination = project_directory / "checkpoint.json"
            if not destination.is_file():
                return False
            destination.unlink()
            return True

    def save_transcript(self, project_id: str, transcript: dict[str, Any]) -> dict[str, Any]:
        """Persist the completed transcript independently of incomplete state."""
        if not isinstance(transcript, dict):
            raise ValueError("transcript must be an object")
        normalized_id = self._validate_id(project_id)
        with self._project_lock(normalized_id):
            _, project_directory = self._require_project(normalized_id)
            saved = self._final_transcript(transcript)
            self._atomic_write_json(project_directory / "transcript.json", saved)
        return saved

    @staticmethod
    def _final_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
        """Keep final transcript records free of incomplete-operation state."""
        return {
            key: copy.deepcopy(value)
            for key, value in transcript.items()
            if key not in {"checkpoint", "is_complete"}
        }

    def load_transcript(self, project_id: str) -> dict[str, Any] | None:
        """Load a completed transcript, copying a legacy result on first open."""
        normalized_id = self._validate_id(project_id)
        with self._project_lock(normalized_id):
            _, project_directory = self._require_project(normalized_id)
            destination = project_directory / "transcript.json"
            if destination.is_file():
                return self._final_transcript(self._read_json(destination))
            if not self.legacy_transcripts_dir:
                return None
            legacy_destination = self.legacy_transcripts_dir / f"{normalized_id}.json"
            try:
                legacy_destination.resolve().relative_to(self.legacy_transcripts_dir.resolve())
            except ValueError as exc:
                raise ValueError("legacy transcript must stay below its configured directory") from exc
            if not legacy_destination.is_file():
                return None
            with legacy_destination.open("r", encoding="utf-8") as transcript_file:
                transcript = json.load(transcript_file)
            if not isinstance(transcript, dict):
                raise ValueError("legacy transcript JSON must contain an object")
            copied = self._final_transcript(transcript)
            self._atomic_write_json(destination, copied)
            return copied
