import { jsonBody, request } from './client'
import type {
  Channel,
  ChannelInput,
  ChannelPreview,
  ActivityProfile,
  EventItem,
  ManualVideoPreview,
  ManualVideoResult,
  QualityPreset,
  RecordingRule,
  SearchResponse,
  StatusInfo,
  Storage,
  StorageUsage,
  Transcript,
  Video,
} from './types'
const query = (values: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams()
  for (const [key, value] of Object.entries(values))
    if (value !== null && value !== undefined && value !== '') p.set(key, String(value))
  return p.toString()
}
export const api = {
  channels: () => request<Channel[]>('/channels'),
  activityProfile: (id: string) => request<ActivityProfile>(`/channels/${id}/activity-profile`),
  setAllPollIntervalsAuto: () =>
    request<{ updated_count: number }>('/channels/poll-interval/auto', jsonBody({})),
  previewChannel: (url: string) => request<ChannelPreview>('/channels/preview', jsonBody({ url })),
  qualityPresets: () => request<QualityPreset[]>('/quality-presets'),
  addChannel: (body: ChannelInput) => request<Channel>('/channels', jsonBody(body)),
  patchChannel: (id: string, body: Partial<ChannelInput>, apply = false) =>
    request<Channel>(`/channels/${id}?apply_retention_to_existing=${apply}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  removeChannel: (id: string) => request<void>(`/channels/${id}`, { method: 'DELETE' }),
  checkChannel: (id: string) => request<void>(`/channels/${id}/check-now`, jsonBody({})),
  refreshChannel: (id: string) => request<Channel>(`/channels/${id}/refresh-metadata`, jsonBody({})),
  storages: () => request<Storage[]>('/storages'),
  usage: () => request<StorageUsage[]>('/storages/usage'),
  addStorage: (body: Partial<Storage>) => request<Storage>('/storages', jsonBody(body)),
  patchStorage: (id: string, body: Partial<Storage>) =>
    request<Storage>(`/storages/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteStorage: (id: string) => request<void>(`/storages/${id}`, { method: 'DELETE' }),
  videos: (params: Record<string, string | number | boolean | null | undefined>) =>
    request<Video[]>(`/videos?${query(params)}`),
  previewManualVideo: (url: string) =>
    request<ManualVideoPreview>('/videos/manual/preview', jsonBody({ url })),
  addManualVideo: (body: { url: string; storage_id?: string | null; protected?: boolean }) =>
    request<ManualVideoResult>('/videos/manual', jsonBody(body)),
  count: (params: Record<string, string | number | boolean | null | undefined>) =>
    request<{ count: number }>(`/videos/count?${query(params)}`),
  video: (id: string) => request<Video>(`/videos/${id}`),
  action: (id: string, suffix: string, method = 'POST') =>
    request<Record<string, unknown>>(`/videos/${id}/${suffix}`, {
      method,
      ...(method === 'POST' ? { body: '{}' } : {}),
    }),
  monitor: (id: string) => request<Record<string, unknown>>(`/videos/${id}/recording/monitor`),
  status: () => request<StatusInfo>('/status'),
  events: (params: Record<string, string | number | undefined> = {}) =>
    request<EventItem[]>(`/events?${query(params)}`),
  search: (params: Record<string, string | number | undefined>) =>
    request<SearchResponse>(`/search/transcripts?${query(params)}`),
  transcript: (id: string, params: Record<string, string | number | undefined> = {}) =>
    request<Transcript>(`/videos/${id}/transcript?${query(params)}`),
  rules: () => request<RecordingRule[]>('/recording-rules'),
  saveRule: (body: Partial<RecordingRule>, id?: string) =>
    request<RecordingRule>(id ? `/recording-rules/${id}` : '/recording-rules', {
      method: id ? 'PUT' : 'POST',
      body: JSON.stringify(body),
    }),
  deleteRule: (id: string) => request<void>(`/recording-rules/${id}`, { method: 'DELETE' }),
  previewRule: (body: Partial<RecordingRule>) =>
    request<Record<string, unknown>>('/recording-rules/preview', jsonBody(body)),
}
