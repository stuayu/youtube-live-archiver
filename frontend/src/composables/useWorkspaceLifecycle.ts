import { onBeforeUnmount, onMounted, watch } from 'vue'
import { api } from '../api/resources'
import { request } from '../api/client'
import type { ChannelStats } from '../api/types'
import type { useWorkspaceState } from './useWorkspaceState'

type WorkspaceState = ReturnType<typeof useWorkspaceState>

export function useWorkspaceLifecycle(
  state: WorkspaceState,
  pageQuery: (scope: string) => Parameters<typeof api.videos>[0],
) {
  let loadGeneration = 0
  const load = async (silent = false) => {
    const generation = ++loadGeneration
    if (silent) state.refreshing.value = true
    else state.loading.value = true
    try {
      if (state.tab.value === 'home') {
        const [status, channels, upcoming, events, storages, usage] = await Promise.all([
          api.status(),
          api.channels(),
          api.videos({ scope: 'upcoming', offset: 0, limit: 30 }),
          api.events({ limit: 10 }),
          api.storages(),
          api.usage(),
        ])
        state.status.value = status
        state.channels.value = channels
        state.upcoming.value = upcoming
        state.events.value = events
        state.storages.value = storages
        state.usage.value = usage
      } else if (state.tab.value === 'channels') {
        ;[state.channels.value, state.storages.value] = await Promise.all([api.channels(), api.storages()])
      } else if (state.tab.value === 'upcoming') {
        const [upcoming, channels] = await Promise.all([
          api.videos({
            scope: 'upcoming',
            offset: 0,
            limit: 100,
            channel_id: state.values.value.channel,
          }),
          api.channels(),
        ])
        if (generation !== loadGeneration) return
        state.upcoming.value = upcoming
        state.channels.value = channels
      } else if (state.tab.value === 'videos') {
        const params = pageQuery(state.scopeDeleted.value ? 'deleted' : 'archive')
        const [rows, count] = await Promise.all([api.videos(params), api.count(params)])
        if (generation !== loadGeneration) return
        state.videos.value = rows
        state.total.value = count.count
        ;[state.storages.value, state.channels.value] = await Promise.all([api.storages(), api.channels()])
        if (generation !== loadGeneration) return
        const recordingRows = rows.filter((video) => video.status === 'RECORDING')
        await Promise.all(
          recordingRows.map(async (video) => {
            try {
              state.metrics.value[video.id] = await api.monitor(video.id)
            } catch {
              // A failed metrics request does not hide the video row.
            }
          }),
        )
      } else if (state.tab.value === 'search') {
        state.channels.value = await api.channels()
      } else if (state.tab.value === 'storages') {
        ;[state.storages.value, state.usage.value] = await Promise.all([api.storages(), api.usage()])
      } else if (state.tab.value === 'analysis') {
        state.channels.value = await api.channels()
        state.analysisChannel.value ||= state.channels.value[0]?.id || ''
        if (state.analysisChannel.value) {
          state.analysisData.value = await request<ChannelStats>(
            `/channels/${state.analysisChannel.value}/stats?from=${new Date(Date.now() - Number(state.analysisPeriod.value) * 86400000).toISOString()}`,
          )
        }
      } else if (state.tab.value === 'activity') {
        state.events.value = await api.events({ limit: 50 })
      } else if (state.tab.value === 'settings') {
        const [rules, transcription, summarySettings, recordingSettings] = await Promise.all([
          api.rules(),
          request<Record<string, unknown>>('/settings/transcription'),
          request<Record<string, unknown>>('/settings/summary'),
          request<Record<string, unknown>>('/settings/recording'),
        ])
        state.rules.value = rules
        state.transcription.value = transcription
        state.summarySettings.value = { ...summarySettings, openrouter_api_key: '' }
        state.recordingSettings.value = recordingSettings
        state.storages.value = await api.storages()
      }
      state.lastUpdated.value = new Intl.DateTimeFormat('ja-JP', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hourCycle: 'h23',
      }).format(new Date())
    } catch (error) {
      state.push(error instanceof Error ? error.message : '読み込みに失敗しました', 'error')
    } finally {
      if (generation === loadGeneration) {
        state.loading.value = false
        state.refreshing.value = false
      }
    }
  }

  let pollTimer = 0
  const schedulePoll = () => {
    window.clearTimeout(pollTimer)
    const interval =
      ['home', 'videos'].includes(state.tab.value) &&
      (state.status.value?.recordings.count ??
        state.videos.value.some((video) => video.status === 'RECORDING'))
        ? 5000
        : 20000
    pollTimer = window.setTimeout(() => {
      if (document.visibilityState === 'visible') void load(true)
      schedulePoll()
    }, interval)
  }
  watch(state.tab, () => {
    state.offset.value = 0
    void load()
    schedulePoll()
  })
  watch(
    [
      () => state.values.value.channel,
      () => state.values.value.q,
      () => state.values.value.vstatus,
      () => state.values.value.reason,
      () => state.values.value.from,
      () => state.values.value.to,
      () => state.values.value.min_size,
      () => state.values.value.max_size,
      () => state.values.value.storage_id,
      () => state.values.value.permanent,
      () => state.values.value.sort,
      () => state.values.value.direction,
    ],
    () => {
      if (state.tab.value === 'videos') {
        state.offset.value = 0
        void load(true)
      }
    },
  )
  const colorPreference = window.matchMedia('(prefers-color-scheme: dark)')
  const applyTheme = () => {
    const dark = state.theme.value === 'dark' || (state.theme.value === 'auto' && colorPreference.matches)
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }
  const preferenceChanged = () => {
    if (state.theme.value === 'auto') applyTheme()
  }
  watch(state.theme, (value) => {
    localStorage.setItem('yla-theme', value)
    applyTheme()
  })
  const visibilityChanged = () => {
    if (document.visibilityState === 'visible') {
      void load(true)
      schedulePoll()
    } else window.clearTimeout(pollTimer)
  }
  onMounted(() => {
    applyTheme()
    colorPreference.addEventListener('change', preferenceChanged)
    void load()
    schedulePoll()
    document.addEventListener('visibilitychange', visibilityChanged)
  })
  onBeforeUnmount(() => {
    window.clearTimeout(pollTimer)
    colorPreference.removeEventListener('change', preferenceChanged)
    document.removeEventListener('visibilitychange', visibilityChanged)
    state.dispose()
  })
  return { load, schedulePoll, visibilityChanged, colorPreference, preferenceChanged, applyTheme }
}
