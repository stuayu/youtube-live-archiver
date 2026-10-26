<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { apiUrl } from '../../api/client'
import type { ChatMessage, ChatPage, Transcript, Video } from '../../api/types'
import { formatBytes, formatDate, formatDuration } from '../../lib/format'
import { videoStatus } from '../../lib/status'
import PlayerSocialTimeline from './PlayerSocialTimeline.vue'

interface TimelineItem {
  start: number
  title: string
  time: string
}

const props = withDefaults(
  defineProps<{
    tab: 'chat' | 'transcript' | 'summary' | 'timeline' | 'info'
    video: Video
    chat: ChatPage
    transcript: Transcript
    timeline: TimelineItem[]
    summary: Record<string, unknown> | null
    error: string
    busy: boolean
    videoId: string
    chatPage: number
    transcriptPage: number
    chatQuery: string
    chatKind: string
    includeDeleted: boolean
    transcriptQuery: string
    playbackSeconds: number
    syncTranscript?: boolean
    transcriptSyncError?: string
    following: boolean
    captureFrame?: () => Promise<{
      data_base64: string
      mime_type: 'image/jpeg'
      width: number
      height: number
      playback_seconds: number
    }>
    storageName?: string
    conversionNote?: string
  }>(),
  {
    syncTranscript: true,
    transcriptSyncError: '',
  },
)
const emit = defineEmits<{
  'update:tab': [value: 'chat' | 'transcript' | 'summary' | 'timeline' | 'info']
  'update:chatQuery': [value: string]
  'update:chatKind': [value: string]
  'update:includeDeleted': [value: boolean]
  'update:transcriptQuery': [value: string]
  'update:syncTranscript': [value: boolean]
  searchChat: []
  retrieveChat: []
  seekCovered: [seconds: number]
  reparseChat: []
  nextChatPage: []
  previousChatPage: []
  runTranscript: []
  searchTranscript: []
  nextTranscriptPage: []
  'sync-transcript': [seconds: number]
  runSummary: [mode: 'all' | 'final']
  seek: [seconds: number]
  followChat: []
  chatScrolled: []
  videoAction: [path: string, method?: string]
}>()
const list = ref<HTMLElement | null>(null)
const transcriptList = ref<HTMLElement | null>(null)
const fallback = ref(new Set<string>())
const mobileSearchOpen = ref(false)
const timelineMode = ref<'highlights' | 'social'>('highlights')
let lastTranscriptSyncRequest = Number.NEGATIVE_INFINITY
const tabs = [
  ['chat', 'チャット'],
  ['transcript', '文字起こし'],
  ['summary', '要約'],
  ['timeline', 'タイムライン'],
  ['info', '情報'],
] as const
const searchable = computed(() => Boolean(props.chatQuery || props.chatKind))
const allRows = computed(() => props.chat.messages)
const replayPending = computed(() => ['queued', 'running'].includes(props.chat.replay?.state || ''))
const activeTranscriptKey = computed(() => {
  if (!props.syncTranscript) return ''
  const segment = props.transcript.segments.find(
    (item) => props.playbackSeconds >= item.start && props.playbackSeconds < item.end,
  )
  return segment ? `${segment.start}-${segment.end}` : ''
})
const transcriptStatusMessage = computed(() => {
  const count = props.transcript.total.toLocaleString()
  switch (props.transcript.status) {
    case 'pending':
      return '文字起こしの開始を待っています。'
    case 'processing':
      return `文字起こし中です。字幕を ${count} 件生成しました。`
    case 'retry_wait':
      return `文字起こしに失敗し、自動再試行を待っています。生成済み字幕は ${count} 件です。`
    case 'failed':
      return '文字起こしに失敗しました。上のボタンから再実行できます。'
    case 'disabled':
      return '文字起こしは無効です。設定画面で有効にしてください。'
    case 'completed':
      return props.transcript.total === 0 ? '処理は完了しましたが、字幕は生成されませんでした。' : ''
    default:
      return ''
  }
})
function replayStatus() {
  const replay = props.chat.replay
  if (!replay) return ''
  if (replay.state === 'queued') return '取得待ち'
  if (replay.state === 'running') return '取得中…'
  if (replay.state === 'waiting' || replay.state === 'rate_limited') {
    return `${replay.state === 'rate_limited' ? 'アクセス制限中' : '取得待ち'}${replay.next_attempt_at ? `（次回 ${formatDate(replay.next_attempt_at)}）` : ''}`
  }
  if (replay.state === 'failed') return `取得できませんでした${replay.error ? `（${replay.error}）` : ''}`
  if (replay.state === 'unavailable') return replay.error || 'YouTube 側でリプレイがまだ用意されていません'
  return ''
}
function fallbackEmote(id: string) {
  fallback.value = new Set(fallback.value).add(id)
}
function membershipLabel(message: ChatMessage) {
  const header = message.header_text || 'メンバーシップ'
  return header.replace(/Member for (\d+) months?/i, 'メンバー $1 か月').replace(/months?/i, 'か月')
}
function authorClass(message: ChatMessage) {
  return `author-${message.author?.role || 'viewer'}`
}
function currencySymbol(currency: string) {
  const symbols: Record<string, string> = {
    JPY: '¥',
    USD: '$',
    CAD: 'CA$',
    AUD: 'A$',
    TWD: 'NT$',
    HKD: 'HK$',
    SGD: 'SGD ',
    KRW: '₩',
    EUR: '€',
    GBP: '£',
    PHP: '₱',
    INR: '₹',
    MYR: 'RM ',
    THB: '฿',
    IDR: 'Rp ',
    MXN: 'MX$',
    BRL: 'R$',
  }
  return symbols[currency] || `${currency} `
}
function formattedTotal(currency: string, amount: number) {
  const fractionDigits = currency === 'JPY' || currency === 'KRW' ? 0 : 2
  return `${currencySymbol(currency)}${amount.toLocaleString('ja-JP', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}`
}
function onScroll() {
  if (list.value && list.value.scrollTop + list.value.clientHeight < list.value.scrollHeight - 48) {
    emit('chatScrolled')
  }
}
function syncTranscriptPage() {
  if (!props.syncTranscript) {
    lastTranscriptSyncRequest = Number.NEGATIVE_INFINITY
    return
  }
  if (props.tab !== 'transcript' || props.transcriptQuery.trim() || !props.transcript.total) return
  const segments = props.transcript.segments
  const first = segments[0]
  const last = segments[segments.length - 1]
  if (!first || !last) return
  const hasPreviousPage = props.transcriptPage > 0
  const hasNextPage = (props.transcriptPage + 1) * 100 < props.transcript.total
  const outsideLoadedPage =
    (hasPreviousPage && props.playbackSeconds < first.start) ||
    (hasNextPage && props.playbackSeconds > last.end)
  if (!outsideLoadedPage) {
    lastTranscriptSyncRequest = Number.NEGATIVE_INFINITY
    return
  }
  if (Math.abs(props.playbackSeconds - lastTranscriptSyncRequest) < 5) return
  lastTranscriptSyncRequest = props.playbackSeconds
  emit('sync-transcript', props.playbackSeconds)
}
watch(
  () =>
    [
      props.playbackSeconds,
      props.transcript.segments,
      props.transcript.total,
      props.transcriptQuery,
      props.tab,
      props.syncTranscript,
    ] as const,
  syncTranscriptPage,
  { immediate: true },
)
watch(
  () => [activeTranscriptKey.value, props.syncTranscript, props.tab] as const,
  async ([key, enabled, activeTab]) => {
    if (!key || !enabled || activeTab !== 'transcript') return
    await nextTick()
    transcriptList.value
      ?.querySelector<HTMLElement>('.transcript-row.is-current')
      ?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  },
  { immediate: true },
)
watch(
  () => [props.chat.messages.length, props.playbackSeconds, props.following] as const,
  async () => {
    if (!props.following || searchable.value) return
    await nextTick()
    if (list.value) list.value.scrollTop = list.value.scrollHeight
  },
)
</script>

<template>
  <aside class="player-panel">
    <nav class="panel-tabs" aria-label="再生情報">
      <button
        v-for="item in tabs"
        :key="item[0]"
        :class="{ active: tab === item[0] }"
        @click="emit('update:tab', item[0])"
      >
        {{ item[1] }}
      </button>
    </nav>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <section v-if="tab === 'chat'" class="panel-content chat-panel">
      <div class="toolbar chat-toolbar" :class="{ 'search-open': mobileSearchOpen }">
        <button
          class="chat-search-toggle"
          aria-label="チャット検索を開く"
          :aria-expanded="mobileSearchOpen"
          @click="mobileSearchOpen = !mobileSearchOpen"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="10.8" cy="10.8" r="6.8" />
            <path d="m16 16 5 5" />
          </svg>
        </button>
        <label class="field chat-search">
          チャット検索
          <input
            :value="chatQuery"
            placeholder="本文・投稿者"
            @input="emit('update:chatQuery', ($event.target as HTMLInputElement).value)"
            @change="emit('searchChat')"
          />
        </label>
        <label class="field chat-kind">
          種別
          <select
            :value="chatKind"
            @change="emit('update:chatKind', ($event.target as HTMLSelectElement).value)"
          >
            <option value="">すべて</option>
            <option value="superchat">スーパーチャット</option>
            <option value="membership">メンバーシップ</option>
          </select>
        </label>
        <label class="check-field deleted-toggle">
          <input
            type="checkbox"
            :checked="includeDeleted"
            @change="emit('update:includeDeleted', ($event.target as HTMLInputElement).checked)"
          />
          削除済みを表示
        </label>
        <details class="chat-menu">
          <summary aria-label="チャット操作">…</summary>
          <div class="chat-menu-items">
            <button :disabled="busy" @click="emit('retrieveChat')">チャットを再取得</button>
            <button :disabled="busy" @click="emit('reparseChat')">チャットを再解析</button>
            <a :href="apiUrl(`/videos/${videoId}/chat/export`)" download>JSON 書き出し</a>
            <label class="check-field chat-menu-deleted">
              <input
                type="checkbox"
                :checked="includeDeleted"
                @change="emit('update:includeDeleted', ($event.target as HTMLInputElement).checked)"
              />
              削除済みを表示
            </label>
          </div>
        </details>
      </div>
      <div v-if="Object.keys(chat.superchat_totals || {}).length" class="superchat-totals">
        <strong>スーパーチャット合計</strong>
        <span v-for="(amount, currency) in chat.superchat_totals" :key="currency">
          {{
            currency === 'unknown'
              ? `${amount.toLocaleString()} 件（金額不明）`
              : formattedTotal(currency, amount)
          }}
        </span>
      </div>
      <button
        v-if="!following && !searchable"
        class="button secondary return-to-chat"
        @click="emit('followChat')"
      >
        現在位置に戻る
      </button>
      <div ref="list" class="chat-list" @scroll.passive="onScroll">
        <p v-if="chat.chat_enabled === false" class="empty-state">このチャンネルではチャット保存が無効です</p>
        <div
          v-else-if="chat.available_total === 0 && chat.replay?.state !== 'imported'"
          class="empty-state replay-empty"
        >
          <p>この時刻のコメントは未取得です</p>
          <p v-if="chat.replay?.covered_from_ms != null">
            取得済み: {{ formatDuration(chat.replay.covered_from_ms / 1000) }} 〜
            {{ formatDuration((chat.replay.covered_to_ms || 0) / 1000) }}（ライブ録画中に取得した分）。
          </p>
          <p>配信全体のコメント（リプレイ）は YouTube 側で用意されると取得できます。</p>
          <p v-if="replayStatus()">{{ replayStatus() }}</p>
          <button
            v-if="chat.replay?.covered_from_ms != null"
            class="button secondary"
            @click="emit('seekCovered', chat.replay.covered_from_ms / 1000)"
          >
            取得済みの位置へ移動
          </button>
          <button
            v-if="chat.replay?.state !== 'disabled'"
            class="button secondary"
            :disabled="replayPending"
            @click="emit('retrieveChat')"
          >
            {{ replayPending ? '取得中…' : '今すぐ再取得' }}
          </button>
        </div>
        <p v-else-if="chat.available_total === 0" class="empty-state">保存されたコメントはありません</p>
        <div
          v-else-if="!allRows.length && chat.replay?.state !== 'imported'"
          class="empty-state replay-empty"
        >
          <p>
            この時刻のコメントは未取得です<span
              v-if="chat.replay?.covered_from_ms !== null && chat.replay?.covered_from_ms !== undefined"
              >。取得済み: {{ formatDuration(chat.replay.covered_from_ms / 1000) }} 〜
              {{ formatDuration((chat.replay.covered_to_ms || 0) / 1000) }}（ライブ録画中に取得した分）</span
            >。
          </p>
          <p>配信全体のコメント（リプレイ）は YouTube 側で用意されると取得できます。</p>
          <p v-if="replayStatus()">{{ replayStatus() }}</p>
          <button
            v-if="chat.replay?.covered_from_ms !== null && chat.replay?.covered_from_ms !== undefined"
            class="button secondary"
            @click="emit('seekCovered', chat.replay.covered_from_ms / 1000)"
          >
            取得済みの位置へ移動
          </button>
          <button
            v-if="chat.replay?.state !== 'disabled'"
            class="button secondary"
            :disabled="replayPending"
            @click="emit('retrieveChat')"
          >
            {{ replayPending ? '取得中…' : '今すぐ再取得' }}
          </button>
        </div>
        <p v-else-if="!allRows.length" class="empty-state">この時刻にチャットはありません</p>
        <article
          v-for="message in allRows"
          :key="message.id"
          class="chat-message"
          :class="[message.message_type, { deleted: message.is_deleted }]"
          :style="message.color ? { '--message-color': message.color } : undefined"
        >
          <template v-if="['superchat', 'supersticker'].includes(message.message_type || '')">
            <header class="paid-header">
              <span class="chat-author-group">
                <span class="author-badges">
                  <template
                    v-for="badge in message.author?.badges || []"
                    :key="`${badge.type}-${badge.label}`"
                  >
                    <img
                      v-if="badge.url"
                      :src="badge.url"
                      :alt="badge.label"
                      :title="badge.label"
                      loading="lazy"
                    />
                    <span v-else class="role-badge" :title="badge.label || badge.type">
                      {{ badge.type === 'owner' ? '配' : badge.type === 'moderator' ? 'モ' : '✓' }}
                    </span>
                  </template>
                </span>
                <strong :class="authorClass(message)">{{
                  message.author?.name || message.author_name
                }}</strong>
              </span>
              <b>{{ message.amount_display }}</b>
              <button
                class="chat-time"
                @click="emit('seek', message.time_seconds ?? message.offset_ms / 1000)"
              >
                {{ formatDuration(message.time_seconds ?? message.offset_ms / 1000) }}
              </button>
            </header>
          </template>
          <template v-else-if="(message.message_type || '').startsWith('membership')">
            <header class="membership-header">
              <span class="chat-author-group">
                <span class="author-badges">
                  <template
                    v-for="badge in message.author?.badges || []"
                    :key="`${badge.type}-${badge.label}`"
                  >
                    <img
                      v-if="badge.url"
                      :src="badge.url"
                      :alt="badge.label"
                      :title="badge.label"
                      loading="lazy"
                    />
                    <span v-else class="role-badge" :title="badge.label || badge.type">
                      {{ badge.type === 'owner' ? '配' : badge.type === 'moderator' ? 'モ' : '✓' }}
                    </span>
                  </template>
                </span>
                <strong :class="authorClass(message)">{{
                  message.author?.name || message.author_name
                }}</strong>
              </span>
              <span class="membership-title">{{ membershipLabel(message) }}</span>
              <button
                class="chat-time"
                @click="emit('seek', message.time_seconds ?? message.offset_ms / 1000)"
              >
                {{ formatDuration(message.time_seconds ?? message.offset_ms / 1000) }}
              </button>
            </header>
          </template>
          <header v-else class="chat-author-line">
            <span class="chat-author-group">
              <span class="author-badges">
                <template v-for="badge in message.author?.badges || []" :key="`${badge.type}-${badge.label}`">
                  <img
                    v-if="badge.url"
                    :src="badge.url"
                    :alt="badge.label"
                    :title="badge.label"
                    loading="lazy"
                  />
                  <span v-else class="role-badge" :title="badge.label || badge.type">
                    {{ badge.type === 'owner' ? '配' : badge.type === 'moderator' ? 'モ' : '✓' }}
                  </span>
                </template>
              </span>
              <strong :class="authorClass(message)">{{ message.author?.name || message.author_name }}</strong>
            </span>
            <button class="chat-time" @click="emit('seek', message.time_seconds ?? message.offset_ms / 1000)">
              {{ formatDuration(message.time_seconds ?? message.offset_ms / 1000) }}
            </button>
          </header>
          <div v-if="message.sticker_url" class="sticker-image">
            <img :src="message.sticker_url" alt="スーパーステッカー" loading="lazy" />
          </div>
          <p class="chat-body">
            <template v-for="(run, index) in message.runs || []" :key="`${message.id}-${index}`">
              <img
                v-if="run.type === 'emote' && run.url && !fallback.has(run.id)"
                class="chat-emote"
                :src="run.url"
                :alt="run.shortcut"
                loading="lazy"
                @error="fallbackEmote(run.id)"
              />
              <span v-else-if="run.type === 'emote'">{{ run.shortcut }}</span>
              <span v-else>{{ run.text }}</span>
            </template>
            <span v-if="!(message.runs || []).length">{{ message.message_text }}</span>
          </p>
          <footer v-if="message.is_deleted" class="deleted-label">削除済み</footer>
        </article>
      </div>
      <button v-if="searchable && chatPage > 0" class="button secondary" @click="emit('previousChatPage')">
        前の 200 件
      </button>
      <button
        v-if="searchable && (chatPage + 1) * 200 < chat.total"
        class="button secondary"
        @click="emit('nextChatPage')"
      >
        次の 200 件
      </button>
    </section>
    <section v-else-if="tab === 'transcript'" class="panel-content">
      <button class="button secondary" :disabled="busy" @click="emit('runTranscript')">
        文字起こしを実行
      </button>
      <p
        v-if="transcriptStatusMessage"
        :class="transcript.status === 'failed' ? 'inline-error' : 'meta'"
        role="status"
        aria-live="polite"
      >
        {{ transcriptStatusMessage }}
      </p>
      <label class="check-field transcript-sync-control">
        <input
          type="checkbox"
          :checked="syncTranscript"
          @change="emit('update:syncTranscript', ($event.target as HTMLInputElement).checked)"
        />
        動画の再生位置に自動追従
      </label>
      <p v-if="transcriptSyncError" class="inline-error" role="alert">{{ transcriptSyncError }}</p>
      <label class="field">
        文字起こし検索
        <input
          :value="transcriptQuery"
          placeholder="キーワード"
          @input="emit('update:transcriptQuery', ($event.target as HTMLInputElement).value)"
          @change="emit('searchTranscript')"
        />
      </label>
      <div ref="transcriptList" class="transcript-segments">
        <button
          v-for="segment in transcript.segments"
          :key="`${segment.start}-${segment.end}`"
          class="transcript-row"
          :class="{ 'is-current': activeTranscriptKey === `${segment.start}-${segment.end}` }"
          :aria-current="activeTranscriptKey === `${segment.start}-${segment.end}` ? 'true' : undefined"
          @click="emit('seek', segment.start)"
        >
          {{ formatDuration(segment.start) }} · {{ segment.text }}
        </button>
      </div>
      <button
        v-if="(transcriptPage + 1) * 100 < transcript.total"
        class="button secondary"
        @click="emit('nextTranscriptPage')"
      >
        次の 100 件
      </button>
    </section>
    <section v-else-if="tab === 'summary'" class="panel-content">
      <p>{{ summary?.status || '要約なし' }}</p>
      <pre v-if="summary?.summary" class="summary-text">{{ JSON.stringify(summary.summary, null, 2) }}</pre>
      <button class="button secondary" :disabled="busy" @click="emit('runSummary', 'all')">要約を生成</button>
      <button class="button secondary" :disabled="busy" @click="emit('runSummary', 'final')">
        全体要約のみ再生成
      </button>
    </section>
    <section v-else-if="tab === 'timeline'" class="panel-content timeline-panel">
      <div class="timeline-mode-tabs" role="tablist" aria-label="タイムラインの表示">
        <button
          type="button"
          role="tab"
          :aria-selected="timelineMode === 'highlights'"
          :class="{ active: timelineMode === 'highlights' }"
          @click="timelineMode = 'highlights'"
        >
          見どころ
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="timelineMode === 'social'"
          :class="{ active: timelineMode === 'social' }"
          @click="timelineMode = 'social'"
        >
          SNS
        </button>
      </div>
      <div v-if="timelineMode === 'highlights'" class="timeline-highlights">
        <button
          v-for="item in timeline"
          :key="`${item.start}-${item.title}`"
          class="transcript-row"
          @click="emit('seek', item.start)"
        >
          {{ item.time }} · {{ item.title }}
        </button>
        <p v-if="!timeline.length" class="empty-state">タイムラインはありません</p>
      </div>
      <PlayerSocialTimeline
        v-else
        :video-id="videoId"
        :youtube-video-id="video.youtube_video_id"
        :playback-seconds="playbackSeconds"
        :capture-frame="captureFrame"
      />
    </section>
    <section v-else class="panel-content player-info-panel">
      <dl class="player-meta">
        <div>
          <dt>録画日時</dt>
          <dd>{{ formatDate(video.recorded_at) }}</dd>
        </div>
        <div>
          <dt>長さ</dt>
          <dd>{{ formatDuration(video.duration_seconds) }}</dd>
        </div>
        <div>
          <dt>容量</dt>
          <dd>{{ formatBytes(video.file_size) }}</dd>
        </div>
        <div>
          <dt>保存先</dt>
          <dd>{{ storageName || '未設定' }}</dd>
        </div>
        <div>
          <dt>保存期限</dt>
          <dd>{{ video.retention_expires_at ? formatDate(video.retention_expires_at) : '永久保存' }}</dd>
        </div>
        <div>
          <dt>状態</dt>
          <dd>
            <span class="badge" :class="videoStatus(video.status).tone">{{
              videoStatus(video.status).label
            }}</span>
          </dd>
        </div>
      </dl>
      <div class="actions">
        <a class="button secondary" :href="apiUrl(`/videos/${video.id}/file?download=true`)">ダウンロード</a>
        <button
          class="button secondary"
          :disabled="busy || !video.has_file"
          @click="emit('videoAction', 'convert/remux')"
        >
          MP4 リマックス
        </button>
        <button
          class="button secondary"
          :disabled="busy || !video.has_file"
          @click="emit('videoAction', 'convert/h264')"
        >
          H.264 変換
        </button>
        <button
          class="button secondary"
          :disabled="busy"
          @click="emit('videoAction', 'protect', video.protected ? 'DELETE' : 'POST')"
        >
          {{ video.protected ? '保護解除' : '保護' }}
        </button>
      </div>
      <p v-if="conversionNote" class="meta" role="status">{{ conversionNote }}</p>
    </section>
  </aside>
</template>
