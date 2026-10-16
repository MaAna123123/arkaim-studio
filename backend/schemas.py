"""HTTP request schemas shared by the API routes."""

from typing import Optional

from pydantic import BaseModel


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
