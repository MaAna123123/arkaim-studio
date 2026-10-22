"""HTTP request schemas shared by the API routes."""

import math

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


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
            raise ValueError("Координаты области должны быть конечными числами")
        if self.x < 0 or self.y < 0 or self.width < 0.03 or self.height < 0.03:
            raise ValueError("Область выходит за границы кадра или слишком мала")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Область должна целиком находиться внутри кадра")
        return self


class NormalizedCropRegion(NormalizedRect):
    """Backward-compatible name used by the streamer layout API."""


class MainVideoLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop: NormalizedRect
    destination: NormalizedRect
    fit: Literal["contain", "cover"] = "cover"

    @field_validator("fit", mode="before")
    @classmethod
    def validate_fit(cls, value):
        if value not in {"contain", "cover"}:
            raise ValueError("Режим вписывания основного видео не поддерживается")
        return value


class TimedOverlayLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    destination: NormalizedRect
    fit: Literal["contain", "cover", "stretch"] = "cover"
    start: float = 0.0
    end: float
    asset_revision: str | None = None

    @field_validator("fit", mode="before")
    @classmethod
    def validate_fit(cls, value):
        if value not in {"contain", "cover", "stretch"}:
            raise ValueError("Режим вписывания видеослоя не поддерживается")
        return value

    @model_validator(mode="after")
    def validate_times(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("Время показа видеослоя должно быть конечным числом")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Некорректный интервал показа видеослоя")
        return self


class AdLayer(TimedOverlayLayer):
    mode: Literal["insert", "overlay"] = "overlay"

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, value):
        if value not in {"insert", "overlay"}:
            raise ValueError("Режим рекламы должен быть insert или overlay")
        return value


class ClipComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_start: float
    source_end: float
    output_aspect_ratio: Literal["original", "9:16"] = "9:16"
    main_layer: MainVideoLayer
    banner_layer: TimedOverlayLayer | None = None
    ad_layer: AdLayer | None = None

    @field_validator("output_aspect_ratio", mode="before")
    @classmethod
    def validate_output_aspect_ratio(cls, value):
        if value not in {"original", "9:16"}:
            raise ValueError("Формат композиции должен быть original или 9:16")
        return value

    @model_validator(mode="after")
    def validate_timeline(self):
        if not math.isfinite(self.source_start) or not math.isfinite(self.source_end):
            raise ValueError("Границы исходного видео должны быть конечными числами")
        duration = self.source_end - self.source_start
        if self.source_start < 0 or duration < 0.5:
            raise ValueError("Некорректные границы исходного видео")
        for layer in (self.banner_layer, self.ad_layer):
            if layer is not None and layer.end > duration:
                raise ValueError("Видеослой выходит за длительность клипа")
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
            raise ValueError("Таймкоды фрагмента должны быть конечными числами")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Некорректные таймкоды фрагмента")
        if self.composition is not None and (
            abs(self.start - self.composition.source_start) > 0.001
            or abs(self.end - self.composition.source_end) > 0.001
        ):
            raise ValueError("Таймкоды фрагмента не совпадают с границами композиции")
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
    streamer_layout_enabled: bool = False
    webcam_region: NormalizedCropRegion | None = None
    gameplay_region: NormalizedCropRegion | None = None
    webcam_height_ratio: float = 0.35

    @model_validator(mode="after")
    def validate_streamer_layout(self):
        if not self.streamer_layout_enabled:
            return self
        if self.aspect_ratio != "9:16":
            raise ValueError("Streamer layout requires 9:16 output")
        if self.webcam_region is None or self.gameplay_region is None:
            raise ValueError("Streamer layout requires webcam and gameplay regions")
        if not math.isfinite(self.webcam_height_ratio) or not 0.2 <= self.webcam_height_ratio <= 0.5:
            raise ValueError("Webcam height ratio must be between 0.2 and 0.5")
        return self


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
    streamer_layout_enabled: bool | None = None
    webcam_region: NormalizedCropRegion | None = None
    gameplay_region: NormalizedCropRegion | None = None
    webcam_height_ratio: float | None = None

    @model_validator(mode="after")
    def validate_streamer_patch(self):
        if self.webcam_height_ratio is not None and (
            not math.isfinite(self.webcam_height_ratio)
            or not 0.2 <= self.webcam_height_ratio <= 0.5
        ):
            raise ValueError("Webcam height ratio must be between 0.2 and 0.5")
        return self
