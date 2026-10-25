from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator, AfterValidator, BeforeValidator, PlainSerializer, ValidationInfo, field_validator
from typing import Annotated
import re


ApiId = Annotated[str, BeforeValidator(lambda value: str(value) if value is not None else value)]


def _validate_storage_path(value: str) -> str:
    if not (value.startswith("/") or value.startswith("\\\\") or re.match(r"^[A-Za-z]:[\\/]", value)):
        raise ValueError("POSIX、Windows ドライブ、または UNC の絶対パスを指定してください")
    return value


def _utc_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UTCDateTime = Annotated[
    datetime,
    AfterValidator(_utc_datetime),
    PlainSerializer(lambda value: _utc_datetime(value).isoformat(), return_type=str),
]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StorageIn(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    root_path: str = Field(min_length=1, max_length=1000)
    enabled: bool = True
    minimum_free_bytes: int = Field(default=0, ge=0)
    warning_free_bytes: int = Field(default=0, ge=0)

    @field_validator("name", "root_path", mode="before")
    @classmethod
    def strip_storage_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("root_path")
    @classmethod
    def absolute_storage_path(cls, value):
        return _validate_storage_path(value)

    @field_validator("warning_free_bytes")
    @classmethod
    def warning_threshold_covers_minimum(cls, value, info: ValidationInfo):
        minimum = info.data.get("minimum_free_bytes", 0)
        if value < minimum:
            raise ValueError("minimum_free_bytes 以上にしてください")
        return value


class StorageOut(StorageIn):
    # Database keys remain integers internally; the HTTP contract uses strings.
    id: ApiId
    reachable: bool | None = None
    writable: bool | None = None
    is_default: bool = False
    model_config = ConfigDict(from_attributes=True)


class StoragePatch(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    root_path: str | None = Field(default=None, min_length=1, max_length=1000)
    enabled: bool | None = None
    minimum_free_bytes: int | None = Field(default=None, ge=0)
    warning_free_bytes: int | None = Field(default=None, ge=0)

    @field_validator("name", "root_path", mode="before")
    @classmethod
    def strip_storage_patch_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("root_path")
    @classmethod
    def absolute_patch_storage_path(cls, value):
        return _validate_storage_path(value) if value is not None else value

    @model_validator(mode="after")
    def validate_storage_patch(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("null は指定できません")
        return self


class ChannelIn(StrictInput):
    youtube_channel_id: str | None = None
    url: str | None = None
    name: str
    enabled: bool = True
    poll_interval: str = "AUTO"
    storage_id: int | None = None
    fallback_storage_id: int | None = None
    retention_days: int | None = 30
    deletion_grace_days: int = 3
    quality: str = "best"
    live_quality: str = ""
    archive_quality: str = "bv*[vcodec^=av01]+ba/bv*[vcodec^=vp9]+ba/bv*+ba/b"
    cookie_profile: str | None = None
    auto_protect_private: bool = True
    auto_protect_deleted: bool = True
    auto_protect_edited: bool = True
    edit_detection_mode: str = "standard"
    always_protect: bool = False
    backfill_limit: int = Field(default=0, ge=0)
    live_playback_enabled: bool = False
    live_playback_limit_bytes: int = Field(default=5368709120, ge=0)
    live_from_start: bool = True
    chat_enabled: bool = True


class ChannelOut(ChannelIn):
    id: ApiId
    storage_id: ApiId | None = None
    fallback_storage_id: ApiId | None = None
    archived: bool = False
    next_check_at: UTCDateTime | None = None
    last_checked_at: UTCDateTime | None = None
    last_success_at: UTCDateTime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    baseline_completed_at: UTCDateTime | None = None
    avatar_url: str | None = None
    has_avatar: bool = False
    banner_url: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    recording_count: int = 0
    description: str | None = None
    model_config = ConfigDict(from_attributes=True)


class VideoOut(BaseModel):
    id: ApiId
    youtube_video_id: str
    channel_id: ApiId
    title: str
    status: str
    remote_status: str
    retention_expires_at: UTCDateTime | None
    protected: bool
    protection_reason: str | None
    protected_at: UTCDateTime | None
    is_partial: bool = False
    recovery_attempts: int = 0
    next_recovery_at: UTCDateTime | None = None
    recording_finished_at: UTCDateTime | None
    file_size: int
    recorded_at: UTCDateTime | None = None
    recording_heartbeat_at: UTCDateTime | None = None
    original_duration: float | None = None
    has_file: bool = False
    live_playback_available: bool = False
    has_thumbnail: bool = False
    thumbnail_url: str | None = None
    scheduled_start_at: UTCDateTime | None = None
    user_stopped: bool = False
    duration_seconds: float | None = None
    deletion_after: UTCDateTime | None = None
    channel_avatar: bool = False
    last_error: str | None = None
    archive_backfill: str | None = None
    segment_count: int = 0
    recovery_exhausted: bool = False
    skip_reason: str | None = None
    cancel_reason: str | None = None
    missed_live: bool = False
    schedule_enabled: bool = True
    live_status: str | None = None
    recording_start_offset_ms: int = 0
    live_from_start: str | None = None
    manual: bool = False
    transcription_status: str = "pending"
    transcription_error: str | None = None
    transcription_model: str | None = None
    transcription_started_at: UTCDateTime | None = None
    transcription_finished_at: UTCDateTime | None = None
    storage_id: ApiId | None = None
    channel_name: str = ""
    storage_name: str | None = None
    media_extension: str | None = None
    media: dict | None = None
    renditions: list[dict] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class ManualVideoPreviewIn(StrictInput):
    url: str = Field(max_length=2000)


class ManualVideoIn(ManualVideoPreviewIn):
    storage_id: int | None = None
    protected: bool = False


class RecordingRuleIn(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    pattern: str = Field(min_length=1, max_length=1000)
    target: str = "both"
    match_mode: str = "keyword"
    is_exclusion: bool = False
    case_sensitive: bool = False
    channel_id: int | None = None
    enabled: bool = True
    storage_id: ApiId | None = None
    retention_days: int | None = Field(default=None, ge=0, le=36500)
    quality: str | None = Field(default=None, max_length=500)
    edit_detection_mode: str | None = None
    priority: int = 100

    @model_validator(mode="after")
    def validate_rule(self):
        if self.target not in ("title", "description", "both"):
            raise ValueError("target は title/description/both のいずれかです")
        if self.match_mode not in ("keyword", "regex"):
            raise ValueError("match_mode は keyword/regex のいずれかです")
        if self.edit_detection_mode is not None and self.edit_detection_mode not in ("off", "basic", "standard", "strong"):
            raise ValueError("edit_detection_mode が不正です")
        if self.match_mode == "regex":
            try:
                re.compile(self.pattern, 0 if self.case_sensitive else re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"正規表現が不正です: {exc}") from exc
        return self


class RecordingRuleOut(RecordingRuleIn):
    id: ApiId
    channel_id: ApiId | None = None
    storage_id: ApiId | None = None
    created_at: UTCDateTime
    model_config = ConfigDict(from_attributes=True)


class ChannelCreate(StrictInput):
    url: str = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=300)
    enabled: bool = True
    poll_interval: str = "AUTO"
    storage_id: int | None = None
    fallback_storage_id: int | None = None
    retention_days: int | None = Field(default=30, ge=1)
    deletion_grace_days: int = Field(default=3, ge=0)
    quality: str = Field(default="best", max_length=100)
    live_quality: str = Field(default="", max_length=500)
    archive_quality: str = Field(default="bv*[vcodec^=av01]+ba/bv*[vcodec^=vp9]+ba/bv*+ba/b", max_length=500)
    cookie_profile: str | None = Field(default=None, max_length=200)
    auto_protect_private: bool = True
    auto_protect_deleted: bool = True
    auto_protect_edited: bool = True
    edit_detection_mode: str = "standard"
    always_protect: bool = False
    backfill_limit: int = Field(default=0, ge=0, le=200)
    live_playback_enabled: bool = False
    live_playback_limit_bytes: int = Field(default=5368709120, ge=0)
    live_from_start: bool = True
    chat_enabled: bool = True

    @field_validator("poll_interval")
    @classmethod
    def poll_valid(cls, value):
        if value not in ("30", "45", "60", "90", "120", "180", "240", "360", "AUTO"):
            raise ValueError("poll_interval は 30/45/60/90/120/180/240/360/AUTO のいずれかです")
        return value

    @field_validator("edit_detection_mode")
    @classmethod
    def mode_valid(cls, value):
        if value not in ("off", "basic", "standard", "strong"):
            raise ValueError("edit_detection_mode が不正です")
        return value


class ChannelPatch(StrictInput):
    url: str | None = None
    youtube_channel_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=300)
    enabled: bool | None = None
    poll_interval: str | None = None
    storage_id: int | None = None
    fallback_storage_id: int | None = None
    retention_days: int | None = None
    deletion_grace_days: int | None = Field(default=None, ge=0)
    quality: str | None = Field(default=None, max_length=100)
    live_quality: str | None = Field(default=None, max_length=500)
    archive_quality: str | None = Field(default=None, max_length=500)
    cookie_profile: str | None = Field(default=None, max_length=200)
    auto_protect_private: bool | None = None
    auto_protect_deleted: bool | None = None
    auto_protect_edited: bool | None = None
    edit_detection_mode: str | None = None
    always_protect: bool | None = None
    backfill_limit: int | None = Field(default=None, ge=0, le=200)
    live_playback_enabled: bool | None = None
    live_playback_limit_bytes: int | None = Field(default=None, ge=0)
    live_from_start: bool | None = None
    chat_enabled: bool | None = None

    @model_validator(mode="after")
    def validate_patch_nulls(self):
        nullable = {"url", "youtube_channel_id", "storage_id", "fallback_storage_id", "retention_days", "cookie_profile"}
        if any(getattr(self, key) is None for key in self.model_fields_set - nullable):
            raise ValueError("null は指定できません")
        return self

    @field_validator("retention_days", mode="before")
    @classmethod
    def empty_retention_is_none(cls, value):
        if value == "":
            return None
        if value is not None and int(value) < 1:
            raise ValueError("retention_days は 1 以上または null です")
        return value

    @field_validator("poll_interval")
    @classmethod
    def patch_poll_valid(cls, value):
        if value is not None and value not in ("30", "45", "60", "90", "120", "180", "240", "360", "AUTO"):
            raise ValueError("poll_interval は 30/45/60/90/120/180/240/360/AUTO のいずれかです")
        return value

    @field_validator("edit_detection_mode")
    @classmethod
    def patch_mode_valid(cls, value):
        if value is not None and value not in ("off", "basic", "standard", "strong"):
            raise ValueError("edit_detection_mode が不正です")
        return value


class TranscriptionSettingsIn(StrictInput):
    transcription_enabled: bool | None = None
    whisper_model: str | None = Field(default=None, min_length=1, max_length=200)
    whisper_device: str | None = None
    whisper_compute_type: str | None = Field(default=None, max_length=100)
    whisper_language: str | None = Field(default=None, max_length=20)
    transcription_max_concurrency: int | None = Field(default=None, ge=1, le=8)

    @field_validator("whisper_device")
    @classmethod
    def device_valid(cls, value):
        if value is not None and value not in ("auto", "cpu", "cuda"):
            raise ValueError("whisper_device が不正です")
        return value

    @model_validator(mode="after")
    def validate_setting_nulls(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("null は指定できません")
        return self


class SummarySettingsIn(StrictInput):
    summary_enabled: bool | None = None
    summary_chunk_minutes: int | None = None
    openrouter_max_concurrency: int | None = Field(default=None, ge=1, le=4)
    openrouter_max_attempts: int | None = Field(default=None, ge=1, le=10)
    openrouter_model: str | None = Field(default=None, max_length=500)
    openrouter_base_url: str | None = Field(default=None, max_length=500)
    openrouter_api_key: str | None = Field(default=None, max_length=1000)

    @field_validator("summary_chunk_minutes")
    @classmethod
    def chunk_valid(cls, value):
        if value is not None and value not in (15, 20, 30, 60):
            raise ValueError("区間は15/20/30/60分です")
        return value

    @model_validator(mode="after")
    def validate_summary_nulls(self):
        if any(getattr(self, key) is None for key in self.model_fields_set - {"openrouter_api_key"}):
            raise ValueError("null は指定できません")
        return self


class ChannelPreviewIn(StrictInput):
    url: str = Field(min_length=1, max_length=1000)
