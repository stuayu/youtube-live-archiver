import { computed } from 'vue'
import { api } from '../api/resources'
import { apiUrl, request } from '../api/client'
import type { Channel, EventItem, Video } from '../api/types'
import type { Tab } from './useQueryState'
import {
  formatBytes,
  formatClock,
  formatDate,
  formatDuration,
  relativeTime,
  bytesToGb,
  gbToBytes,
} from '../lib/format'
import {
  monitorErrorSummary,
  monitorStatus,
  protectionReason,
  recordFailureReason,
  videoStatus,
} from '../lib/status'
import { useAnalysisWorkspace } from './useAnalysisWorkspace'
import { useChannelWorkspace } from './useChannelWorkspace'
import { useSettingsWorkspace } from './useSettingsWorkspace'
import { useStorageWorkspace } from './useStorageWorkspace'
import { useUpcomingWorkspace } from './useUpcomingWorkspace'
import { useVideoWorkspace } from './useVideoWorkspace'
import { useWorkspaceLifecycle } from './useWorkspaceLifecycle'
import { useWorkspaceState } from './useWorkspaceState'

export function useWorkspace() {
  const state = useWorkspaceState()
  const { tab, values, events, status, videos, total, offset, pageSize, channelState } = state
  function pageQuery(scope: string) {
    return {
      scope,
      offset: offset.value,
      limit: pageSize,
      channel_id: values.value.channel,
      q: values.value.q,
      status: values.value.vstatus,
      protection_reason: values.value.reason,
      from: values.value.from,
      to: values.value.to,
      min_size: values.value.min_size ? gbToBytes(Number(values.value.min_size)) : undefined,
      max_size: values.value.max_size ? gbToBytes(Number(values.value.max_size)) : undefined,
      storage_id: values.value.storage_id,
      permanent: values.value.permanent,
      sort: values.value.sort || 'date',
      direction: values.value.direction || 'desc',
    }
  }
  const { load, schedulePoll } = useWorkspaceLifecycle(state, pageQuery)
  function setTab(value: Tab) {
    if (value === tab.value) return
    tab.value = value
    void load()
  }
  function refresh() {
    void load(true)
  }
  function refreshAnalysis() {
    void load()
  }
  function confirm(title: string, body: string, run: () => Promise<void>) {
    state.confirmState.value = { title, body, run }
  }
  async function executeConfirm() {
    const action = state.confirmState.value
    if (!action) return
    state.confirmState.value = null
    try {
      await action.run()
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '操作に失敗しました', 'error')
    }
  }
  function saveChannel(channel: Channel) {
    state.editingChannel.value = undefined
    state.push(`${channel.name} を保存しました`)
    void load(true)
  }
  function removeChannel(channel: Channel) {
    confirm(
      'チャンネルの監視解除',
      `${channel.name} の監視を停止します。録画ファイルは削除しません。`,
      async () => {
        await api.removeChannel(channel.id)
        state.push('監視を解除しました')
      },
    )
  }
  async function checkChannel(channel: Channel) {
    try {
      await api.checkChannel(channel.id)
      state.push('確認を予約しました')
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '確認できません', 'error')
    }
  }
  async function refreshChannel(channel: Channel) {
    try {
      await api.refreshChannel(channel.id)
      state.push('チャンネル情報を更新しました')
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '更新できません', 'error')
    }
  }
  function openVideo(video: Video, live = video.status === 'RECORDING', start = 0) {
    state.playerVideo.value = video
    state.playerLive.value = live
    state.playerStart.value = start
  }
  const storageWorkspace = useStorageWorkspace(state, load, confirm)
  const settingsWorkspace = useSettingsWorkspace(state, load, confirm)
  const videoWorkspace = useVideoWorkspace(state, load, confirm, setTab, openVideo)
  const channelWorkspace = useChannelWorkspace(state)
  const upcomingWorkspace = useUpcomingWorkspace(state)
  const analysisWorkspace = useAnalysisWorkspace(state)
  const recordings = computed(() => status.value?.recordings.items ?? [])
  const latestEvents = computed(() => events.value.slice(0, 10))
  const pageStart = computed(() => (total.value ? offset.value + 1 : 0))
  const pageEnd = computed(() => Math.min(offset.value + videos.value.length, total.value))
  function archiveRetentionLabel(video: Video) {
    if (video.protected || video.retention_expires_at === null) return '保存期限なし（永久）'
    const days = Math.ceil((Date.parse(video.retention_expires_at) - Date.now()) / 86400000)
    return days > 0
      ? `保存期限 あと ${days} 日`
      : `保存期限 削除予定 ${formatDate(video.deletion_after || video.retention_expires_at).slice(5, 10)}`
  }
  function openAttention(code: string) {
    if (code.startsWith('storage_')) setTab('storages')
    else if (code === 'monitor_errors') {
      channelState.value = 'error'
      setTab('channels')
    } else if (code === 'recording_failures') {
      values.value.vstatus = 'RECORDING_FAILED'
      setTab('videos')
    } else if (code.endsWith('_missing')) {
      setTab('home')
      window.setTimeout(() => document.getElementById('system-details')?.scrollIntoView(), 0)
    }
  }
  function goToArchive(status?: string) {
    values.value.vstatus = status
    offset.value = 0
    setTab('videos')
  }
  function labelForEvent(event: EventItem) {
    const names: Record<string, string> = {
      recording_completed: '録画が完了しました',
      recording_started: '録画を開始しました',
      RECORD: '録画が完了しました',
      MANUAL_RETRY: '手動で録画を再試行しました',
      RECORD_FAILED: `録画に失敗しました: ${recordFailureReason(event.reason)}`,
      PROTECT: `保護しました: ${protectionReason(event.reason)}`,
      MONITOR_ERROR: `監視エラー: ${monitorErrorSummary(event.reason)}`,
      SALVAGE_PARTIAL: '部分録画を保存しました',
      DELETE: '保存期間により削除しました',
      MANUAL_DELETE: '手動で削除しました',
      RECOVERY_QUEUED: '録画の復旧を予約しました',
      LIVE_SEGMENT_INTERRUPTED: 'ライブ録画が中断しました',
      video_protected: `保護しました: ${protectionReason(event.reason)}`,
      protected: `保護しました: ${protectionReason(event.reason)}`,
      monitor_error: `監視エラー: ${monitorErrorSummary(event.reason)}`,
      video_deleted: '動画を削除しました',
      recording_failed: `録画に失敗しました: ${recordFailureReason(event.reason)}`,
      channel_added: 'チャンネルを追加しました',
      channel_updated: 'チャンネル設定を更新しました',
    }
    return (
      names[event.action] ?? `${event.action.replace(/_/g, ' ')}${event.reason ? `: ${event.reason}` : ''}`
    )
  }
  function formatRetention(value: number | null) {
    return value === null ? '永久' : `${value} 日`
  }

  return {
    ...state,
    api,
    apiUrl,
    request,
    formatBytes,
    formatClock,
    formatDate,
    formatDuration,
    relativeTime,
    bytesToGb,
    gbToBytes,
    monitorStatus,
    monitorErrorSummary,
    protectionReason,
    videoStatus,
    pageQuery,
    load,
    schedulePoll,
    setTab,
    refresh,
    refreshAnalysis,
    confirm,
    executeConfirm,
    saveChannel,
    removeChannel,
    checkChannel,
    refreshChannel,
    openVideo,
    recordings,
    latestEvents,
    pageStart,
    pageEnd,
    archiveRetentionLabel,
    openAttention,
    goToArchive,
    labelForEvent,
    formatRetention,
    ...channelWorkspace,
    ...upcomingWorkspace,
    ...videoWorkspace,
    ...storageWorkspace,
    ...settingsWorkspace,
    ...analysisWorkspace,
  }
}
