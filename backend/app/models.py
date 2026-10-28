from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, BigInteger, String, Text, Float, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return datetime.now(timezone.utc)


class Storage(Base):
    __tablename__ = "storages"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    root_path: Mapped[str] = mapped_column(String(1000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    minimum_free_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    warning_free_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(1000))
    name: Mapped[str] = mapped_column(String(300))
    avatar_url: Mapped[str | None] = mapped_column(String(2000))
    avatar_cache_path: Mapped[str | None] = mapped_column(String(2000))
    banner_url: Mapped[str | None] = mapped_column(String(2000))
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    video_count: Mapped[int | None] = mapped_column(BigInteger)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    backfill_limit: Mapped[int] = mapped_column(Integer, default=0)
    live_playback_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    live_playback_limit_bytes: Mapped[int] = mapped_column(BigInteger, default=5368709120)
    live_from_start: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    chat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    poll_interval: Mapped[str] = mapped_column(String(20), default="360")
    poll_windows: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    storage_id: Mapped[int | None] = mapped_column(ForeignKey("storages.id"))
    fallback_storage_id: Mapped[int | None] = mapped_column(ForeignKey("storages.id"))
    retention_days: Mapped[int | None] = mapped_column(Integer, default=30)
    deletion_grace_days: Mapped[int] = mapped_column(Integer, default=3)
    quality: Mapped[str] = mapped_column(String(100), default="best")
    live_quality: Mapped[str] = mapped_column(String(500), default="")
    archive_quality: Mapped[str] = mapped_column(String(500), default="bv*[vcodec^=av01]+ba/bv*[vcodec^=vp9]+ba/bv*+ba/b")
    cookie_profile: Mapped[str | None] = mapped_column(String(200))
    auto_protect_private: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_protect_deleted: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_protect_edited: Mapped[bool] = mapped_column(Boolean, default=True)
    edit_detection_mode: Mapped[str] = mapped_column(String(20), default="standard")
    always_protect: Mapped[bool] = mapped_column(Boolean, default=False)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    baseline_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # backfill の基準日（登録より前の配信を何本取り込むかの判定に使う）。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    storage = relationship("Storage", foreign_keys=[storage_id])
    fallback_storage = relationship("Storage", foreign_keys=[fallback_storage_id])

    @property
    def has_avatar(self) -> bool:
        return bool(self.avatar_cache_path)


class ChannelStat(Base):
    __tablename__ = "channel_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    subscriber_count_rounded: Mapped[bool] = mapped_column(Boolean, default=False)
    video_count: Mapped[int | None] = mapped_column(BigInteger)
    view_count: Mapped[int | None] = mapped_column(BigInteger)


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_video_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    storage_id: Mapped[int | None] = mapped_column(ForeignKey("storages.id"))
    edit_detection_mode: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(1000), default="")
    thumbnail_url: Mapped[str | None] = mapped_column(String(2000))
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    integrity_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    format_selector: Mapped[str | None] = mapped_column(Text)
    format_profile: Mapped[str | None] = mapped_column(String(20))
    format_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_format: Mapped[str | None] = mapped_column(String(500))
    transcription_status: Mapped[str] = mapped_column(String(20), default="pending")
    transcription_error: Mapped[str | None] = mapped_column(Text)
    transcription_model: Mapped[str | None] = mapped_column(String(200))
    transcription_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcription_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_status: Mapped[str] = mapped_column(String(20), default="disabled")
    summary_data: Mapped[dict | None] = mapped_column(JSON)
    summary_error: Mapped[str | None] = mapped_column(Text)
    summary_model: Mapped[str | None] = mapped_column(String(200))
    original_duration: Mapped[float | None] = mapped_column(Float)
    remote_status: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    retention_days: Mapped[int | None] = mapped_column(Integer)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    protected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    protection_reason: Mapped[str | None] = mapped_column(String(60))
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_recovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    protected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="DISCOVERED", index=True)
    deletion_candidate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_path: Mapped[str | None] = mapped_column(String(2000))
    live_hls_path: Mapped[str | None] = mapped_column(String(2000))
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    raw_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    channel = relationship("Channel")
    storage = relationship("Storage")
    remote_checks = relationship("RemoteCheck", cascade="all, delete-orphan")
    fingerprints = relationship("Fingerprint", cascade="all, delete-orphan")
    transcript_segments = relationship("TranscriptSegment", cascade="all, delete-orphan")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("video_id", "kind"),)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    raw: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    __table_args__ = (UniqueConstraint("video_id", "start", "end", "raw"),)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class RecordingRule(Base):
    __tablename__ = "recording_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    pattern: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(String(20), default="both")  # title, description, both
    match_mode: Mapped[str] = mapped_column(String(20), default="keyword")  # keyword, regex
    is_exclusion: Mapped[bool] = mapped_column(Boolean, default=False)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    storage_id: Mapped[int | None] = mapped_column(ForeignKey("storages.id"))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    quality: Mapped[str | None] = mapped_column(String(500))
    edit_detection_mode: Mapped[str | None] = mapped_column(String(20))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SummaryCache(Base):
    __tablename__ = "summary_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    offset_ms: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author_channel_id: Mapped[str | None] = mapped_column(String(100))
    author_name: Mapped[str] = mapped_column(String(500), default="")
    author_badges: Mapped[list] = mapped_column(JSON, default=list)
    message_runs: Mapped[list] = mapped_column(JSON, default=list)
    author_role: Mapped[str] = mapped_column(String(30), default="viewer")
    message_text: Mapped[str] = mapped_column(Text, default="")
    amount_display: Mapped[str | None] = mapped_column(String(100))
    header_text: Mapped[str | None] = mapped_column(Text)
    sticker_url: Mapped[str | None] = mapped_column(String(2000))
    superchat_amount: Mapped[float | None] = mapped_column(Float)
    superchat_currency: Mapped[str | None] = mapped_column(String(10))
    superchat_color: Mapped[str | None] = mapped_column(String(20))
    message_type: Mapped[str] = mapped_column(String(40), default="normal")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_source: Mapped[str | None] = mapped_column(String(40))


class RemoteCheck(Base):
    __tablename__ = "remote_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    remote_status: Mapped[str] = mapped_column(String(40))
    remote_duration: Mapped[float | None] = mapped_column(Float)
    edit_detected: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    raw_metadata: Mapped[dict | None] = mapped_column(JSON)


class Fingerprint(Base):
    __tablename__ = "fingerprints"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    position: Mapped[float] = mapped_column(Float)
    video_hash: Mapped[str | None] = mapped_column(String(256))
    audio_hash: Mapped[str | None] = mapped_column(String(256))
    __table_args__ = (UniqueConstraint("video_id", "position"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), index=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
