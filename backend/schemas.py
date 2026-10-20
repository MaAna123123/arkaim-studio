"""HTTP request schemas shared by the API routes."""

import math

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class NormalizedRect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    width: float
    height: float

    @model_validator(mode="after")
    def validate_bounds(self):
        values = (self.x, self.y, self.width, self.height)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Crop coordinates must be finite")
        if self.x < 0 or self.y < 0 or self.width < 0.03 or self.height < 0.03:
            raise ValueError("Crop region is outside the frame or too small")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Crop region must stay inside the frame")
        return self


class NormalizedCropRegion(NormalizedRect):
    """Backward-compatible name used by the streamer layout API."""


class MainVideoLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop: NormalizedRect
    destination: NormalizedRect
    fit: Literal["contain", "cover"] = "cover"


class TimedOverlayLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    destination: NormalizedRect
    fit: Literal["contain", "cover", "stretch"] = "cover"
    start: float = 0.0
    end: float

    @model_validator(mode="after")
    def validate_times(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("Overlay times must be finite")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Overlay time range is invalid")
        return self


class AdLayer(TimedOverlayLayer):
    mode: Literal["insert", "overlay"] = "overlay"


class ClipComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_start: float
    source_end: float
    output_aspect_ratio: Literal["original", "9:16"] = "9:16"
    main_layer: MainVideoLayer
    banner_layer: TimedOverlayLayer | None = None
    ad_layer: AdLayer | None = None

    @model_validator(mode="after")
    def validate_timeline(self):
        if not math.isfinite(self.source_start) or not math.isfinite(self.source_end):
            raise ValueError("Source times must be finite")
        duration = self.source_end - self.source_start
        if self.source_start < 0 or duration < 0.5:
            raise ValueError("Source time range is invalid")
        for layer in (self.banner_layer, self.ad_layer):
            if layer is not None and layer.end > duration:
                raise ValueError("Overlay exceeds the edited clip duration")
        return self


class ExportSegment(BaseModel):
    model_config = ConfigDict(extra="allow")

    start: float
    end: float
    label: str = ""
    composition: ClipComposition | None = None

    @model_validator(mode="after")
    def validate_segment_times(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("Segment times must be finite")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Segment time range is invalid")
        if self.composition is not None and (
            abs(self.start - self.composition.source_start) > 0.001
            or abs(self.end - self.composition.source_end) > 0.001
        ):
            raise ValueError("Segment times must match composition source times")
        return self


class AnalyzeRequest(BaseModel):
    video_id: str
    prompt: str = ""
    mode: str = "transcript"
    chunk_minutes: float = 4.0


class ExportRequest(BaseModel):
    video_id: str
    segments: list[ExportSegment]
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
