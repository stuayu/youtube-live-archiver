from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator, BeforeValidator
from typing import Annotated
import re


ApiId = Annotated[str, BeforeValidator(lambda value: str(value) if value is not None else value)]


class StorageIn(BaseModel):
    name: str
    root_path: str
    enabled: bool = True
    minimum_free_bytes: int = 0
    warning_free_bytes: int = 0


class StorageOut(StorageIn):
    # Database keys remain integers internally; the HTTP contract uses strings.
    id: ApiId
    model_config = ConfigDict(from_attributes=True)


class ChannelIn(BaseModel):
    youtube_channel_id: str | None = None
    url: str | None = None
    name: str
    enabled: bool = True
    poll_interval: str = "30"
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


class ChannelOut(ChannelIn):
    id: ApiId
    storage_id: ApiId | None = None
    fallback_storage_id: ApiId | None = None
    archived: bool = False
    next_check_at: datetime | None = None
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
    retention_expires_at: datetime | None
    protected: bool
    protection_reason: str | None
    protected_at: datetime | None
    is_partial: bool = False
    recovery_attempts: int = 0
    next_recovery_at: datetime | None = None
    recording_finished_at: datetime | None
    file_size: int
    recorded_at: datetime | None = None
    recording_heartbeat_at: datetime | None = None
    original_duration: float | None = None
    has_file: bool = False
    live_playback_available: bool = False
    has_thumbnail: bool = False
    thumbnail_url: str | None = None
    scheduled_start_at: datetime | None = None
    raw_metadata: dict = {}
    transcription_status: str = "pending"
    transcription_error: str | None = None
    transcription_model: str | None = None
    transcription_started_at: datetime | None = None
    transcription_finished_at: datetime | None = None
    storage_id: int | None = None
    channel_name: str = ""
    storage_name: str | None = None
    media_extension: str | None = None
    model_config = ConfigDict(from_attributes=True)


class RecordingRuleIn(BaseModel):
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
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
