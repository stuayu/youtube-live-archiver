import { api } from '../api/resources'
import { request } from '../api/client'
import type { Video } from '../api/types'
import type { QueryKey } from './useQueryState'
import { protectionReason, videoStatus } from '../lib/status'
import type { useWorkspaceState } from './useWorkspaceState'

type WorkspaceState = ReturnType<typeof useWorkspaceState>
type ConfirmAction = (title: string, body: string, run: () => Promise<void>) => void
type LoadData = (silent?: boolean) => Promise<void>
type SetTab = (
  tab:
    | 'home'
    | 'channels'
    | 'upcoming'
    | 'videos'
    | 'search'
    | 'storages'
    | 'analysis'
    | 'activity'
    | 'settings',
) => void
type OpenVideo = (video: Video, live?: boolean, start?: number) => void

export function useVideoWorkspace(
  state: WorkspaceState,
  load: LoadData,
  confirm: ConfirmAction,
  setTab: SetTab,
  openVideo: OpenVideo,
) {
  function videoBadges(video: Video): Array<{ label: string; tone: string }> {
    const status = videoStatus(video.status)
    const badges = [{ label: status.label, tone: status.tone }]
    if (video.protected) {
      badges.push({
        label: `保護: ${protectionReason(video.protection_reason) || '永久保存'}`,
        tone: 'protected',
      })
    }
    if (video.is_partial) badges.push({ label: '部分録画', tone: 'warning' })
    if (video.user_stopped) badges.push({ label: '手動停止', tone: 'neutral' })
    if (video.archive_backfill === 'queued') badges.push({ label: 'アーカイブ補完中', tone: 'info' })
    if (video.missed_live) badges.push({ label: '配信後に取得', tone: 'warning' })
    if (video.recovery_exhausted) badges.push({ label: '自動復旧を停止', tone: 'danger' })
    if (!video.has_file && state.tab.value === 'videos')
      badges.push({ label: 'ファイルなし', tone: 'warning' })
    return badges
  }

  async function toggleSchedule(video: Video) {
    try {
      await api.action(video.id, `schedule/${video.schedule_enabled ? 'disable' : 'enable'}`)
      state.push(video.schedule_enabled ? '録画予約を解除しました' : '録画予約しました')
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '予約を更新できません', 'error')
    }
  }

  async function stopRecording(video: Pick<Video, 'id'>) {
    state.pendingStops.value.add(video.id)
    try {
      await api.action(video.id, 'recording/stop')
      state.push('録画停止を要求しました')
      for (let attempt = 0; attempt < 12; attempt++) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        const fresh = await api.video(video.id)
        if (fresh.status !== 'RECORDING') break
      }
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '停止できません', 'error')
    } finally {
      state.pendingStops.value.delete(video.id)
    }
  }

  async function deleteVideo(video: Video) {
    confirm(
      '録画ファイルを削除',
      `「${video.title}」とローカル録画ファイルを削除します。この操作は元に戻せません。`,
      async () => {
        await request<void>(`/videos/${video.id}`, { method: 'DELETE' })
        state.push('録画ファイルを削除しました')
      },
    )
  }

  async function toggleProtection(video: Video) {
    try {
      await api.action(video.id, 'protect', video.protected ? 'DELETE' : 'POST')
      state.push(video.protected ? '保護を解除しました' : '永久保存に設定しました')
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '保護状態を変更できません', 'error')
    }
  }

  async function convertVideo(video: Video, mode: 'remux' | 'h264') {
    try {
      await api.action(video.id, `convert/${mode}`)
      state.push(mode === 'remux' ? 'MP4 リマックスを開始しました' : 'H.264 変換を開始しました')
    } catch (error) {
      state.push(error instanceof Error ? error.message : '変換を開始できません', 'error')
    }
  }

  function page(direction: number) {
    state.offset.value = Math.max(0, state.offset.value + direction * state.pageSize)
    void load()
  }

  function clearFilter(key: QueryKey) {
    state.values.value[key] = undefined
  }

  function setArchiveScope(deleted: boolean) {
    state.scopeDeleted.value = deleted
    state.offset.value = 0
    void load()
  }

  async function runSearch() {
    if (!state.searchQuery.value.trim()) {
      state.searchResults.value = { total: 0, offset: 0, limit: 30, results: [] }
      return
    }
    try {
      state.searchResults.value = await api.search({
        q: state.searchQuery.value.trim(),
        channel_id: state.values.value.channel,
        offset: state.searchOffset.value,
        limit: 30,
      })
    } catch (error) {
      state.push(error instanceof Error ? error.message : '検索できません', 'error')
    }
  }

  function setSearchPage(direction: number) {
    state.searchOffset.value = Math.max(0, state.searchOffset.value + direction * 30)
    void runSearch()
  }

  function submitSearch() {
    state.searchOffset.value = 0
    void runSearch()
  }

  async function openSearchResult(item: { video_id: string; title: string; start: number }) {
    try {
      const video = await api.video(item.video_id)
      setTab('videos')
      openVideo(video, false, item.start)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '動画を開けません', 'error')
    }
  }

  return {
    videoBadges,
    toggleSchedule,
    stopRecording,
    deleteVideo,
    toggleProtection,
    convertVideo,
    page,
    clearFilter,
    setArchiveScope,
    runSearch,
    setSearchPage,
    submitSearch,
    openSearchResult,
  }
}
