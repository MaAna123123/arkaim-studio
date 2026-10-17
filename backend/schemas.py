"""HTTP request schemas shared by the API routes."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class AnalyzeRequest(BaseModel):
    video_id: str
    prompt: str = ""
    mode: str = "transcript"
    chunk_minutes: float = 4.0


class ExportRequest(BaseModel):
    video_id: str
    segments: list[dict]
    mode: str = "separate"
    padding: float = 0.5
    aspect_ratio: str = "original"
    burn_subtitles: bool = True
    insert_ad: bool = False
    text_ad_enabled: bool = False
    text_ad_text: str = ""
    text_ad_position: str = "top"
    text_ad_interval: float = 4.0
    text_ad_opacity: float = 0.75
    video_banner_enabled: bool = False
    video_banner_height: int = 380


class SocialUploadRequest(BaseModel):
    platform: str
    clip_url: str
    title: str = "Untitled"
    description: str = ""
    tags: list[str] = []
    privacy: str = "private"


class SettingsRequest(BaseModel):
    provider: Optional[str] = "gemini"
    api_key: Optional[str] = ""
    model_name: Optional[str] = ""
    base_url: Optional[str] = ""
    gemini_api_key: Optional[str] = ""
    whisper_model: Optional[str] = ""


class YoutubeUrlRequest(BaseModel):
    url: str
    quality: str = "720"


class AutoPipelineRequest(BaseModel):
    """One request: URL to ready clips."""

    url: str
    quality: str = "720"
    prompt: str = ""
    aspect_ratio: str = "9:16"
    burn_subtitles: bool = True
    padding: float = 0.4
    max_clips: int = 10
    insert_ad: bool = False
    text_ad_enabled: bool = False
    text_ad_text: str = ""
    text_ad_position: str = "top"
    text_ad_interval: float = 4.0
    text_ad_opacity: float = 0.75


class SocialPrepareRequest(BaseModel):
    platform: str
    clip_url: str
    label: str = ""
    transcript_text: str = ""


class TranscribeRequest(BaseModel):
    video_id: str
    language: Optional[str] = None


class ProjectEditorPatch(BaseModel):
    """Allow-listed editor state; project/runtime identity is never client-settable."""

    model_config = ConfigDict(extra="forbid")

    segments: Any | None = None
    selected: Any | None = None
    clip_results: Any | None = None
    subtitle_context: Any | None = None
    subtitle_notice: Any | None = None
    export_padding: Any | None = None
    aspect_ratio: Any | None = None
    burn_subtitles: Any | None = None
    insert_ad: Any | None = None
    text_ad_enabled: Any | None = None
    text_ad_text: Any | None = None
    text_ad_position: Any | None = None
    text_ad_interval: Any | None = None
    text_ad_opacity: Any | None = None
    video_banner_enabled: Any | None = None
    video_banner_height: Any | None = None
