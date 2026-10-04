export type Id = string
export type VideoStatus =
  | 'WAITING_LIVE'
  | 'DISCOVERED'
  | 'RECORDING'
  | 'ACTIVE'
  | 'RECORDING_FAILED'
  | 'WAITING_FOR_STORAGE'
  | 'VERIFYING_REMOTE'
  | 'DELETE_ELIGIBLE'
  | 'PROTECTION_PENDING'
  | 'PROTECTED'
  | 'SKIPPED'
  | 'CANCELLED'
  | 'DELETED_BY_RETENTION'
  | 'DELETED_MANUALLY'
  | string
export type MonitorState = 'paused' | 'recording' | 'error' | 'pending' | 'ok'
export interface ApiError extends Error {
  status: number
  fields: Record<string, string>
}
export interface Channel {
  id: Id
  youtube_channel_id: string
  url: string
  name: string
  enabled: boolean
  poll_interval: string
  storage_id: Id | null
  fallback_storage_id: Id | null
  retention_days: number | null
  deletion_grace_days: number
  quality: string
  live_quality: string
  archive_quality: string
  cookie_profile: string | null
  auto_protect_private: boolean
  auto_protect_deleted: boolean
  auto_protect_edited: boolean
  edit_detection_mode: string
  always_protect: boolean
  backfill_limit: number
  live_playback_enabled: boolean
  live_from_start?: boolean
  live_playback_limit_bytes: number
  chat_enabled: boolean
  archived: boolean
  next_check_at: string | null
  last_checked_at: string | null
  last_success_at: string | null
  last_error: string | null
  consecutive_failures: number
  avatar_url?: string | null
  has_avatar: boolean
  subscriber_count?: number | null
  video_count?: number | null
  recording_count: number
  recording_now_count?: number
  upcoming_count?: number
  failed_count?: number
  monitor_state: MonitorState
  description?: string | null
}
export interface ActivityProfile {
  samples: number
  mode: 'insufficient' | 'hour' | 'weekhour'
  hours: string[]
  levels: string[]
  current_level: string
  next_check_at: string | null
  normal_interval_seconds: [number, number]
  hot_ranges: string[]
}
export interface Storage {
  id: Id
  name: string
  root_path: string
  enabled: boolean
  minimum_free_bytes: number
  warning_free_bytes: number
  reachable?: boolean | null
  writable?: boolean | null
  is_default?: boolean
}
export interface StorageUsage {
  id: Id
  name: string
  root_path: string
  total_bytes: number | null
  free_bytes: number | null
  used_bytes: number | null
  app_bytes: number
  recording_count: number
  reachable: boolean
  writable: boolean
  minimum_free_bytes: number
  warning_free_bytes: number
  warning: boolean
  critical: boolean
}
export interface Video {
  id: Id
  youtube_video_id: string
  channel_id: Id
  title: string
  manual?: boolean
  status: VideoStatus
  remote_status: string
  retention_expires_at: string | null
  protected: boolean
  protection_reason: string | null
  protected_at: string | null
  is_partial: boolean
  recovery_attempts: number
  next_recovery_at: string | null
  recording_finished_at: string | null
  file_size: number
  recorded_at: string | null
  recording_heartbeat_at: string | null
  original_duration: number | null
  has_file: boolean
  live_playback_available: boolean
  has_thumbnail: boolean
  thumbnail_url: string | null
  scheduled_start_at: string | null
  user_stopped: boolean
  duration_seconds: number | null
  deletion_after: string | null
  last_error: string | null
  archive_backfill: string | null
  recording_start_offset_ms?: number
  live_from_start?: 'used' | 'fallback' | 'skipped_too_old' | null
  segment_count: number
  recovery_exhausted: boolean
  skip_reason: string | null
  cancel_reason: string | null
  missed_live: boolean
  schedule_enabled: boolean
  live_status: string | null
  transcription_status: string
  transcription_error: string | null
  storage_id: Id | null
  channel_name: string
  storage_name: string | null
  media_extension: string | null
  playable?: boolean
  media?: {
    container: string
    video_codec: string | null
    audio_codec: string | null
    width: number | null
    height: number | null
  } | null
  renditions?: Array<{
    name: 'source' | 'remux' | 'h264'
    url: string
    container: string
    video_codec: string | null
    audio_codec: string | null
    available: boolean
    size: number | null
    status?: string
    progress?: number
  }>
}
export interface ManualVideoPreview {
  video_id: string
  title: string
  thumbnail: string | null
  channel: { youtube_channel_id: string; name: string; registered: boolean; monitored: boolean }
  live_status: string | null
  scheduled_start_at: string | null
  duration: number | null
  action: 'record_live' | 'wait_live' | 'download_archive'
  action_label: string
  existing: { id: string; status: string; status_label: string } | null
}
export interface ManualVideoResult {
  video: Video
  action: ManualVideoPreview['action']
  created: boolean
  channel_created: boolean
}
export interface Page<T> {
  items: T[]
  total: number
}
export type ChannelInput = Omit<
  Partial<Channel>,
  | 'id'
  | 'youtube_channel_id'
  | 'archived'
  | 'next_check_at'
  | 'last_checked_at'
  | 'last_success_at'
  | 'last_error'
  | 'consecutive_failures'
  | 'has_avatar'
  | 'recording_count'
  | 'recording_now_count'
  | 'upcoming_count'
  | 'failed_count'
  | 'monitor_state'
  | 'avatar_url'
  | 'subscriber_count'
  | 'video_count'
  | 'description'
> & { url?: string; name?: string; retention_days?: number | null }
export interface ChannelPreview {
  channel_id: string
  url: string
  name: string
  avatar?: string | null
}
export interface QualityPreset {
  id: string
  label: string
  selector: string
  description: string
}
export interface ChannelStatPoint {
  captured_at: string
  subscriber_count: number | null
  subscriber_count_rounded: boolean
  video_count: number | null
  view_count: number | null
}
export interface RecordingStats {
  count: number
  total_bytes: number
  protected_count: number
  protected_reasons: Record<string, number>
  average_duration_seconds: number | null
}
export interface ChannelStats {
  channel_id: Id
  from: string
  to: string
  granularity: 'day' | 'week' | 'month'
  points: ChannelStatPoint[]
  current: ChannelStatPoint | null
  deltas: Record<string, number | null>
  recordings: RecordingStats
}
export interface EventItem {
  id: Id
  video_id: Id | null
  channel_id: Id | null
  action: string
  reason: string | null
  details: Record<string, unknown> | null
  created_at: string
  video_title: string | null
  channel_name: string | null
}
export interface StatusInfo {
  database: { available: boolean; mode?: 'external' | 'embedded'; data_dir?: string }
  scheduler: {
    running: boolean
    jobs: Array<{
      id: string
      name: string
      next_run_at?: string | null
      last_finished_at?: string | null
      last_error?: string | null
    }>
  }
  tools: Record<string, unknown>
  monitor: {
    enabled_channel_count: number
    error_channel_count: number
    cooldown_remaining_seconds: number
    oldest_last_success_at: string | null
  }
  recordings: {
    count: number
    items: Array<{
      video_id: Id
      title: string
      channel_name: string
      started_at: string | null
      heartbeat_at: string | null
    }>
    waiting_count: number
    live_slots_used: number
    live_slots_limit: number
    archive_slots_used: number
    archive_slots_limit: number
  }
  counts: Record<string, number>
  storages: { unreachable: string[]; low_space: string[]; warning_space: string[] }
  attention: Array<{ level: string; code: string; message: string }>
}
export interface TranscriptSegment {
  start: number
  end: number
  text: string
}
export interface Transcript {
  status: string
  error: string | null
  total: number
  segments: TranscriptSegment[]
}
export interface ChatMessage {
  id: Id
  offset_ms: number
  time_seconds?: number
  author_name: string
  message_text: string
  is_deleted: boolean
  message_type?:
    | 'normal'
    | 'superchat'
    | 'supersticker'
    | 'membership'
    | 'membership_gift'
    | 'membership_gift_received'
    | string
  runs?: ChatRun[]
  author?: ChatAuthor
  amount_display?: string | null
  amount?: number | null
  currency?: string | null
  color?: string | null
  header_text?: string | null
  sticker_url?: string | null
}
export type ChatRun =
  { type: 'text' | 'emoji'; text: string } | { type: 'emote'; id: string; shortcut: string; url: string }
export interface ChatBadge {
  type: string
  label: string
  url: string
}
export interface ChatAuthor {
  name: string
  role: string
  badges: ChatBadge[]
}
export interface ChatPage {
  messages: ChatMessage[]
  total: number
  available_total?: number
  chat_enabled?: boolean
  chat_replay?: string | null
  replay?: {
    state:
      'imported' | 'queued' | 'running' | 'waiting' | 'unavailable' | 'rate_limited' | 'failed' | 'disabled'
    checked_at: string | null
    next_attempt_at: string | null
    attempts: number
    error: string | null
    covered_from_ms: number | null
    covered_to_ms: number | null
  }
  superchat_totals?: Record<string, number>
  unknown_amounts?: Record<string, number>
}
export interface RecordingRule {
  id: Id
  name: string
  pattern: string
  target: string
  match_mode: string
  is_exclusion: boolean
  case_sensitive: boolean
  channel_id: Id | null
  enabled: boolean
  storage_id: Id | null
  retention_days: number | null
  quality: string | null
  edit_detection_mode: string | null
  priority: number
  created_at?: string
}
export interface SearchResult {
  video_id: Id
  title: string
  channel_name: string
  start: number
  end: number
  text: string
}
export interface SearchResponse {
  total: number
  offset: number
  limit: number
  results: SearchResult[]
}
