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
from typing import Any


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
            subscription_url = value.casefold().startswith(("http://", "https://")) and "subscription" in value.casefold()
            if path_like or subscription_url:
                return False
        return True

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
            if field in video and self._safe_scalar(value):
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

        source_path = project.get("source_path", "")
        if not isinstance(source_path, str) or Path(source_path).is_absolute():
            project.pop("source_path", None)
        project["video"] = self._safe_video_metadata(project.get("video"))
        return project
