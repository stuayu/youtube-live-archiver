import { ref } from 'vue'
import type {
  Channel,
  ChannelStats,
  EventItem,
  RecordingRule,
  SearchResponse,
  StatusInfo,
  Storage,
  StorageUsage,
  Video,
} from '../api/types'
import type { IconName } from '../components/ui/Icon.vue'
import { useQueryState, type QueryKey, type Tab } from './useQueryState'
import { useToast } from './useToast'

export function useWorkspaceState() {
  const { tab, values, dispose } = useQueryState()
  values.value.sort ||= 'date'
  values.value.direction ||= 'desc'
  const { push } = useToast()
  const channels = ref<Channel[]>([])
  const storages = ref<Storage[]>([])
  const usage = ref<StorageUsage[]>([])
  const videos = ref<Video[]>([])
  const upcoming = ref<Video[]>([])
  const events = ref<EventItem[]>([])
  const status = ref<StatusInfo | null>(null)
  const rules = ref<RecordingRule[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const lastUpdated = ref('')
  const pageSize = 30
  const offset = ref(0)
  const total = ref(0)
  const scopeDeleted = ref(false)
  const filtersOpen = ref(false)
  const searchText = ref(values.value.q || '')
  const channelSearch = ref('')
  const channelState = ref('all')
  const channelSort = ref('name')
  const upcomingSearch = ref('')
  const upcomingFrom = ref('')
  const upcomingTo = ref('')
  const searchQuery = ref('')
  const searchResults = ref<SearchResponse>({ total: 0, offset: 0, limit: 30, results: [] })
  const searchOffset = ref(0)
  const playerVideo = ref<Video | null>(null)
  const playerLive = ref(false)
  const playerStart = ref(0)
  const editingChannel = ref<Channel | null | undefined>(undefined)
  const editingStorage = ref<Storage | null>(null)
  const showStorageDialog = ref(false)
  const confirmState = ref<{
    title: string
    body: string
    run: () => Promise<void>
  } | null>(null)
  const pendingStops = ref(new Set<string>())
  const metrics = ref<Record<string, Record<string, unknown>>>({})
  const storageForm = ref({ name: '', root_path: '', minimumGb: 0, warningGb: 0, enabled: true })
  const analysisChannel = ref('')
  const analysisPeriod = ref('90')
  const analysisData = ref<ChannelStats | null>(null)
  const formatId = ref('')
  const formats = ref('')
  const ruleForm = ref<Partial<RecordingRule>>({
    name: '',
    pattern: '',
    target: 'both',
    match_mode: 'keyword',
    is_exclusion: false,
    case_sensitive: false,
    channel_id: null,
    enabled: true,
    storage_id: null,
    retention_days: null,
    quality: null,
    edit_detection_mode: null,
    priority: 100,
  })
  const ruleEditing = ref<string | undefined>(undefined)
  const rulePreview = ref<Record<string, unknown> | null>(null)
  const transcription = ref<Record<string, unknown>>({})
  const summarySettings = ref<Record<string, unknown>>({})
  const recordingSettings = ref<Record<string, unknown>>({})
  const theme = ref<'auto' | 'light' | 'dark'>(
    (localStorage.getItem('yla-theme') as 'auto' | 'light' | 'dark') || 'auto',
  )
  const mainTabs: Array<{ id: Tab; label: string; icon: IconName }> = [
    { id: 'home', label: 'ホーム', icon: 'home' },
    { id: 'channels', label: 'チャンネル', icon: 'channels' },
    { id: 'upcoming', label: '配信予定', icon: 'upcoming' },
    { id: 'videos', label: 'アーカイブ', icon: 'archive' },
    { id: 'search', label: '全文検索', icon: 'search' },
    { id: 'storages', label: 'ストレージ', icon: 'storage' },
    { id: 'analysis', label: '分析', icon: 'analysis' },
    { id: 'activity', label: 'アクティビティ', icon: 'activity' },
    { id: 'settings', label: '設定', icon: 'settings' },
  ]
  const mobileTabs = mainTabs.slice(0, 4)
  const moreTabs = mainTabs.slice(4)
  const archiveFilterKeys: QueryKey[] = [
    'channel',
    'q',
    'vstatus',
    'reason',
    'from',
    'to',
    'min_size',
    'max_size',
    'storage_id',
    'permanent',
  ]

  return {
    tab,
    values,
    dispose,
    push,
    channels,
    storages,
    usage,
    videos,
    upcoming,
    events,
    status,
    rules,
    loading,
    refreshing,
    lastUpdated,
    pageSize,
    offset,
    total,
    scopeDeleted,
    filtersOpen,
    searchText,
    channelSearch,
    channelState,
    channelSort,
    upcomingSearch,
    upcomingFrom,
    upcomingTo,
    searchQuery,
    searchResults,
    searchOffset,
    playerVideo,
    playerLive,
    playerStart,
    editingChannel,
    editingStorage,
    showStorageDialog,
    confirmState,
    pendingStops,
    metrics,
    storageForm,
    analysisChannel,
    analysisPeriod,
    analysisData,
    formatId,
    formats,
    ruleForm,
    ruleEditing,
    rulePreview,
    transcription,
    summarySettings,
    recordingSettings,
    theme,
    mainTabs,
    mobileTabs,
    moreTabs,
    archiveFilterKeys,
  }
}
