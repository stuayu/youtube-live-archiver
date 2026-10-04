from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = ""
    storage_root: str = "./data/archive"
    live_quality: str = ""
    archive_quality: str = "bv*[vcodec^=av01]+ba/bv*[vcodec^=vp9]+ba/bv*+ba/b"
    data_dir: str = "./data"
    auto_migrate: bool = True
    database_connect_retry_seconds: float = 30
    yt_dlp_path: str = "yt-dlp"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    playable_cache_days: int = 14
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    # ソース変更を検知して自動再起動する。サービス常駐中でも有効にできるが、
    # 再起動時に進行中の録画サブプロセスが巻き込まれるため、既定は無効。
    app_reload: bool = False
    app_reload_dirs: str = "app"
    scheduler_enabled: bool = True
    db_idle_in_transaction_timeout_ms: int = 60000
    downloader: Literal["native", "aria2c"] = "native"
    # yt-dlp の --concurrent-fragments。断片化された HLS/DASH では
    # aria2c より native + -N のほうが速く安定する。
    concurrent_fragments: int = 16
    aria2c_path: str = "aria2c"
    aria2c_connections: int = 16
    aria2c_split: int = 16
    aria2c_min_split_size: str = "1M"
    aria2c_max_concurrent_downloads: int = 4
    channel_stats_interval_hours: int = 24
    remote_timeout_seconds: int = 45
    monitor_batch_size: int = 20
    monitor_concurrency: int = 3
    monitor_max_inspects_per_scan: int = 5
    max_concurrent_live_recordings: int = 8
    max_concurrent_archive_downloads: int = 2
    recovery_max_attempts: int = 20
    recorder_shutdown_timeout_seconds: int = 20
    live_gap_backfill: bool = True
    live_gap_backfill_threshold_seconds: int = 120
    live_from_start: bool = True
    live_from_start_max_age_hours: int = 12
    storage_retry_seconds: int = 60
    integrity_batch_size: int = 200
    duration_tolerance_seconds: int = 30
    fingerprint_enabled: bool = True
    fingerprint_video_threshold: float = 0.25
    fingerprint_audio_threshold: float = 0.35
    transcription_enabled: bool = True
    whisper_model: str = "large-v3-turbo"
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"
    whisper_compute_type: str = "auto"
    whisper_language: str = ""
    transcription_max_concurrency: int = 1
    processing_max_attempts: int = 5
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    summary_enabled: bool = False
    summary_chunk_minutes: int = 30
    openrouter_max_concurrency: int = 1
    openrouter_max_attempts: int = 5


settings = Settings()

# Resolve relative data paths against the backend directory once, so service
# and local launches use the same stable locations regardless of process cwd.
_backend_dir = Path(__file__).resolve().parents[1]


def _backend_path(value: str) -> str:
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else _backend_dir / path).resolve())


settings.data_dir = _backend_path(settings.data_dir)
settings.storage_root = _backend_path(settings.storage_root)
