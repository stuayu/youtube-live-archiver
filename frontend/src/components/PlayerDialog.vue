<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiUrl, request } from '../api/client'
import type { ChatPage, Transcript, Video } from '../api/types'
import { installDPlayerSettings } from '../lib/dplayerSettings'
import type { DPlayerSettingsHandle } from '../lib/dplayerSettings'
import { readDanmakuPreferences, writeDanmakuPreferences } from '../lib/playerSettings'
import { chatReplayRange, danmakuRange } from '../lib/playerPlayback'
import { choosePlayback, type PlaybackRendition } from '../lib/playback'
import PlayerHeader from './player/PlayerHeader.vue'
import PlayerPanels from './player/PlayerPanels.vue'
import PlayerVideoInfo from './player/PlayerVideoInfo.vue'
const props = defineProps<{
  video: Video
  live: boolean
  initialTime?: number
}>()
const emit = defineEmits<{
  close: []
  finished: [video: Video]
  updated: []
}>()
const mount = ref<HTMLElement | null>(null)
const videoEl = ref<HTMLVideoElement | null>(null)
const tab = ref<'chat' | 'transcript' | 'summary' | 'timeline' | 'info'>('chat')
const chat = ref<ChatPage>({ messages: [], total: 0 })
const transcript = ref<Transcript>({ status: 'pending', error: null, total: 0, segments: [] })
const timeline = ref<
  Array<{
    start: number
    title: string
    time: string
  }>
>([])
const summary = ref<Record<string, unknown> | null>(null)
const chatQuery = ref('')
const transcriptQuery = ref('')
const chatKind = ref('')
const includeDeleted = ref(false)
const chatPage = ref(0)
const transcriptPage = ref(0)
const syncTranscript = ref(true)
const transcriptSyncError = ref('')
const savedPreferences = readDanmakuPreferences(localStorage, window.matchMedia('(max-width: 620px)').matches)
chatKind.value = savedPreferences.danmakuKind
includeDeleted.value = savedPreferences.includeDeleted
const danmakuOn = ref(savedPreferences.enabled)
const opacity = ref(savedPreferences.opacity)
const speed = ref(savedPreferences.speed)
const area = ref(savedPreferences.area)
const font = ref(savedPreferences.font)
const quantity = ref(savedPreferences.quantity)
const playbackRate = ref(savedPreferences.playbackRate)
const loop = ref(savedPreferences.loop)
const liveFollow = ref(savedPreferences.liveFollow)
const playbackSeconds = ref(0)
const followingChat = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const playbackMessage = ref('')
const playbackProgress = ref(0)
const playbackActionRequired = ref(false)
const playbackFailed = ref(false)
const conversionNote = ref('')
const delay = ref<number | null>(null)
async function captureCurrentFrame() {
  const video = videoEl.value
  if (
    !video ||
    video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA ||
    !video.videoWidth ||
    !video.videoHeight
  ) {
    throw new Error('映像の読み込み後にキャプチャしてください')
  }
  const capturedAt = video.currentTime
  let width = video.videoWidth
  let height = video.videoHeight
  const initialScale = Math.min(1, 1280 / width, 1280 / height)
  width = Math.max(1, Math.round(width * initialScale))
  height = Math.max(1, Math.round(height * initialScale))
  const sourceCanvas = document.createElement('canvas')
  sourceCanvas.width = width
  sourceCanvas.height = height
  const sourceContext = sourceCanvas.getContext('2d', { alpha: false })
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d', { alpha: false })
  if (!sourceContext || !context) throw new Error('画像を作成できませんでした')
  let blob: Blob | null = null
  let quality = 0.86
  try {
    sourceContext.drawImage(video, 0, 0, width, height)
    for (let resize = 0; resize < 5; resize += 1) {
      canvas.width = width
      canvas.height = height
      context.drawImage(sourceCanvas, 0, 0, width, height)
      for (const level of [quality, 0.76, 0.66, 0.56]) {
        blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', level))
        if (blob && blob.size <= 1_900_000) break
      }
      if (blob && blob.size <= 1_900_000) break
      width = Math.max(1, Math.round(width * 0.8))
      height = Math.max(1, Math.round(height * 0.8))
      quality = 0.76
    }
  } catch {
    throw new Error('この映像はブラウザーの制限で画像化できません。再生映像を読み直して試してください')
  }
  if (!blob || blob.size > 1_900_000)
    throw new Error('画像を2 MB以下にできませんでした。別の場面で試してください')
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000))
  }
  return {
    data_base64: btoa(binary),
    mime_type: 'image/jpeg' as const,
    width,
    height,
    playback_seconds: capturedAt,
  }
}
let player: import('dplayer').default | undefined
let hls: import('hls.js').default | undefined
let hlsRetryTimer: ReturnType<typeof setTimeout> | undefined
let playerSettings: DPlayerSettingsHandle | undefined
let chatTimer = 0
let chatRequestSequence = 0
let transcriptRequestSequence = 0
let transcriptTimer = 0
let playbackTimer = 0
let monitorTimer = 0
let initialized = false
let focusBefore: HTMLElement | null = null
let metadataTimer = 0
let prepareSequence = 0
let backgroundSequence = 0
let danmakuWindow = { afterMs: -1, beforeMs: -1 }
let requestedDanmakuKey = ''
function danmakuAddress(seconds = playbackSeconds.value) {
  const range = danmakuRange(seconds)
  danmakuWindow = range
  requestedDanmakuKey = `${range.afterMs}:${range.beforeMs}:${chatKind.value}:${includeDeleted.value}`
  return apiUrl(
    `/videos/${props.video.id}/chat/danmaku/${chatKind.value || 'all'}-${includeDeleted.value ? '1' : '0'}-window-${range.afterMs}-${range.beforeMs}/`,
  )
}
async function loadChat(atSeconds = playbackSeconds.value) {
  const p = new URLSearchParams({
    offset: String((chatQuery.value || chatKind.value ? chatPage.value : 0) * 200),
    limit: chatQuery.value || chatKind.value ? '200' : '300',
    include_deleted: String(includeDeleted.value),
  })
  if (chatQuery.value) p.set('q', chatQuery.value)
  if (chatKind.value) p.set('kind', chatKind.value)
  if (!chatQuery.value && !chatKind.value) {
    const range = chatReplayRange(atSeconds)
    p.set('after_ms', String(range.afterMs))
    p.set('before_ms', String(range.beforeMs))
  }
  const sequence = ++chatRequestSequence
  const result = await request<ChatPage>(`/videos/${props.video.id}/chat?${p}`)
  if (sequence === chatRequestSequence) chat.value = result
}
async function loadTranscript(atSeconds?: number) {
  const p = new URLSearchParams({ offset: String(transcriptPage.value * 100), limit: '100' })
  if (transcriptQuery.value) p.set('q', transcriptQuery.value)
  if (atSeconds !== undefined && !transcriptQuery.value) p.set('at_seconds', String(atSeconds))
  const sequence = ++transcriptRequestSequence
  const result = await request<Transcript>(`/videos/${props.video.id}/transcript?${p}`)
  if (sequence !== transcriptRequestSequence) return
  if (
    atSeconds !== undefined &&
    (!syncTranscript.value || tab.value !== 'transcript' || transcriptQuery.value)
  )
    return
  transcript.value = result
  updateTranscriptPolling(result.status)
  if (atSeconds !== undefined && typeof result.offset === 'number') {
    transcriptPage.value = Math.floor(result.offset / 100)
  }
}
function updateTranscriptPolling(status: string) {
  if (['pending', 'processing', 'retry_wait'].includes(status)) {
    if (!transcriptTimer) {
      transcriptTimer = window.setInterval(() => {
        if (document.visibilityState === 'hidden') return
        void loadTranscript().catch(() => {})
      }, 10_000)
    }
    return
  }
  window.clearInterval(transcriptTimer)
  transcriptTimer = 0
}
async function syncTranscriptAt(seconds: number) {
  if (!syncTranscript.value || tab.value !== 'transcript' || transcriptQuery.value) return
  transcriptSyncError.value = ''
  try {
    await loadTranscript(seconds)
  } catch {
    if (syncTranscript.value && tab.value === 'transcript' && !transcriptQuery.value)
      transcriptSyncError.value = '再生位置に合わせた文字起こしを読み込めませんでした'
  }
}
function searchChat() {
  chatPage.value = 0
  followingChat.value = !chatQuery.value && !chatKind.value
  void loadChat()
}
function nextChatPage() {
  chatPage.value += 1
  void loadChat()
}
function previousChatPage() {
  chatPage.value = Math.max(0, chatPage.value - 1)
  void loadChat()
}
function searchTranscript() {
  transcriptPage.value = 0
  void loadTranscript()
}
function nextTranscriptPage() {
  transcriptPage.value += 1
  void loadTranscript()
}
watch(syncTranscript, (enabled) => {
  if (!enabled) transcriptSyncError.value = ''
})
async function loadPanels() {
  const [chatResult, transcriptResult, timelineResult, summaryResult] = await Promise.allSettled([
    loadChat(),
    loadTranscript(),
    request<{
      items: Array<{
        start: number
        title: string
        time: string
      }>
    }>(`/videos/${props.video.id}/timeline`),
    request<Record<string, unknown>>(`/videos/${props.video.id}/summary`),
  ])
  if (timelineResult.status === 'fulfilled') timeline.value = timelineResult.value.items
  if (summaryResult.status === 'fulfilled') summary.value = summaryResult.value
  for (const result of [chatResult, transcriptResult])
    if (result.status === 'rejected')
      error.value = result.reason instanceof Error ? result.reason.message : '情報を取得できません'
}
function followLive() {
  const el = videoEl.value
  if (el?.seekable.length) {
    el.currentTime = Math.max(
      el.seekable.start(el.seekable.length - 1),
      el.seekable.end(el.seekable.length - 1) - 1.5,
    )
    void el.play().catch(() => {})
  }
}
watch(
  [danmakuOn, opacity, speed, area, font, quantity, chatKind, includeDeleted, playbackRate, loop, liveFollow],
  () => {
    writeDanmakuPreferences(localStorage, {
      enabled: danmakuOn.value,
      opacity: opacity.value,
      speed: speed.value,
      area: area.value,
      font: font.value,
      quantity: quantity.value,
      danmakuKind: chatKind.value,
      includeDeleted: includeDeleted.value,
      playbackRate: playbackRate.value,
      loop: loop.value,
      liveFollow: liveFollow.value,
    })
  },
)
watch(danmakuOn, (enabled) => playerSettings?.setDanmakuEnabled(enabled))
function seek(seconds: number) {
  playbackSeconds.value = Math.max(0, seconds)
  reloadDanmaku(seconds)
  if (followingChat.value && !chatQuery.value && !chatKind.value) void loadChat(seconds)
  const el = videoEl.value
  if (!el) return
  const setTime = () => {
    el.currentTime = Math.max(0, seconds)
    playbackSeconds.value = Math.max(0, seconds)
    void el.play().catch(() => {})
  }
  if (el.readyState >= 1) setTime()
  else el.addEventListener('loadedmetadata', setTime, { once: true })
}
async function makePlayer(renditionName?: PlaybackRendition['name'], startTime = 0) {
  if (!mount.value) return
  window.clearTimeout(metadataTimer)
  playbackFailed.value = false
  playbackActionRequired.value = false
  if (!props.live && !renditionName) {
    const decision = choosePlayback(props.video.media, props.video.renditions || [], (type) =>
      document.createElement('video').canPlayType(type),
    )
    if (decision.rendition === 'h264') {
      const available = props.video.renditions?.find((item) => item.name === 'h264')?.available
      if (!available) {
        const codecs = `${props.video.media?.video_codec || '不明'} / ${props.video.media?.audio_codec || '不明'}`
        playbackMessage.value =
          `このブラウザでは再生できない形式です（${codecs}）。` +
          'H.264 に変換すると再生できます（時間が掛かります）'
        playbackActionRequired.value = true
        return
      }
    }
    if (decision.rendition === 'remux') {
      const remux = props.video.renditions?.find((item) => item.name === 'remux')
      if (!remux?.available) {
        await prepareRendition('remux')
        return
      }
    }
    renditionName = decision.rendition
  }
  const [{ default: DPlayer }, hlsModule] = await Promise.all([import('dplayer'), import('hls.js')])
  const Hls = hlsModule.default
  const rendition = props.video.renditions?.find((item) => item.name === renditionName)
  const url = props.live
    ? apiUrl(`/videos/${props.video.id}/live/index.m3u8`)
    : apiUrl(rendition?.url || `/videos/${props.video.id}/file`)
  playbackMessage.value = ''
  player = new DPlayer({
    container: mount.value,
    lang: 'ja',
    theme: '#1681a5',
    autoplay: false,
    hotkey: true,
    loop: loop.value,
    video: {
      url,
      type: props.live ? 'customHls' : 'auto',
      customType: props.live
        ? {
            customHls: (element) => {
              videoEl.value = element
              const preferHlsJs = Hls.isSupported() && /Chrome|Chromium/.test(navigator.userAgent)
              if (preferHlsJs || !element.canPlayType('application/vnd.apple.mpegurl')) {
                if (!Hls.isSupported()) return
                hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2 })
                hls.on(Hls.Events.ERROR, (_event, data) => {
                  if (!data.fatal) return
                  if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                    hls?.recoverMediaError()
                  } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                    if (hlsRetryTimer) clearTimeout(hlsRetryTimer)
                    hlsRetryTimer = setTimeout(() => hls?.startLoad(), 1000)
                  }
                })
                hls.loadSource(url)
                hls.attachMedia(element)
              } else {
                element.src = url
              }
            },
          }
        : undefined,
    },
    danmaku: {
      id: props.video.id,
      api: danmakuAddress(props.initialTime || 0),
      token: '',
      maximum:
        quantity.value === 'few'
          ? 35
          : quantity.value === 'many'
            ? 160
            : quantity.value === 'unlimited'
              ? 1000
              : 80,
      addition: [],
      user: 'viewer',
      bottom: area.value,
      unlimited: false,
      mutex: true,
      speedRate: 12 / speed.value,
    },
    contextmenu: [],
    highlight: [],
  })
  videoEl.value = player.video
  videoEl.value.playbackRate = playbackRate.value
  videoEl.value.loop = loop.value
  playerSettings = installDPlayerSettings({
    root: mount.value,
    player,
    initial: {
      enabled: danmakuOn.value,
      opacity: opacity.value,
      speed: speed.value,
      area: area.value,
      font: font.value,
      quantity: quantity.value,
      danmakuKind: chatKind.value,
      includeDeleted: includeDeleted.value,
      playbackRate: playbackRate.value,
      loop: loop.value,
      liveFollow: liveFollow.value,
    },
    live: props.live,
    onFollowLive: followLive,
    onChange: (value) => {
      danmakuOn.value = value.enabled
      opacity.value = value.opacity
      speed.value = value.speed
      area.value = value.area
      font.value = value.font
      quantity.value = value.quantity
      chatKind.value = value.danmakuKind
      includeDeleted.value = value.includeDeleted
      playbackRate.value = value.playbackRate
      loop.value = value.loop
      liveFollow.value = value.liveFollow
    },
  })
  const seekTime = startTime || props.initialTime || 0
  if (seekTime > 0) seek(seekTime)
  const update = () => {
    const el = videoEl.value
    if (el) playbackSeconds.value = el.currentTime
    if (
      el &&
      (el.currentTime * 1000 < danmakuWindow.afterMs ||
        el.currentTime * 1000 > danmakuWindow.beforeMs - 30_000)
    )
      reloadDanmaku(el.currentTime)
    if (props.live && el?.seekable.length)
      delay.value = Math.max(0, el.seekable.end(el.seekable.length - 1) - el.currentTime)
    playerSettings?.setLiveDelay(delay.value)
  }
  videoEl.value.addEventListener('timeupdate', update)
  videoEl.value.addEventListener(
    'error',
    () => {
      if (!props.live) showPlaybackFailure()
    },
    { once: true },
  )
  if (!props.live) {
    watchLoading(videoEl.value)
    void videoEl.value.play().catch(() => {})
  }
  playbackTimer = window.setInterval(() => {
    update()
    if (props.live && liveFollow.value && delay.value !== null && delay.value > 3) followLive()
    if (
      (followingChat.value || ['queued', 'running'].includes(chat.value.replay?.state || '')) &&
      !chatQuery.value &&
      !chatKind.value
    )
      void loadChat()
  }, 2000)
  if (props.live) {
    monitorTimer = window.setInterval(async () => {
      try {
        const latest = await request<Video>(`/videos/${props.video.id}`)
        if (latest.status !== 'RECORDING') {
          emit('finished', latest)
          destroy()
        }
      } catch {
        /* keep live player if status polling is temporarily unavailable */
      }
    }, 5000)
  }
}
// A paused video is not a failure: browsers may block autoplay after the conversion wait.
function watchLoading(element: HTMLVideoElement) {
  if (element.readyState >= 1) return
  element.addEventListener(
    'loadedmetadata',
    () => {
      if (element !== videoEl.value) return
      window.clearTimeout(metadataTimer)
      if (!playbackFailed.value) return
      playbackFailed.value = false
      playbackMessage.value = ''
    },
    { once: true },
  )
  metadataTimer = window.setTimeout(() => {
    if (element === videoEl.value && element.readyState < 1) showPlaybackFailure()
  }, 12000)
}
async function waitForConversion(
  name: 'remux' | 'h264',
  active: () => boolean,
  onProgress: (percent: number) => void,
) {
  const start = () => request(`/videos/${props.video.id}/convert/${name}`, { method: 'POST', body: '{}' })
  await start()
  let restarts = 0
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000))
    if (!active()) return false
    const status = await request<{ status: string; progress?: number }>(
      `/videos/${props.video.id}/convert/${name}`,
    )
    onProgress(Math.round((status.progress || 0) * 100))
    if (status.status === 'failed') throw new Error('動画変換に失敗しました')
    if (status.status === 'completed') return true
    // The job is gone (for example the backend restarted): start it again instead of waiting forever.
    if (status.status === 'idle') {
      if (++restarts > 3) throw new Error('動画変換を開始できません')
      await start()
    }
  }
}
async function prepareRendition(name: 'remux' | 'h264') {
  const sequence = ++prepareSequence
  window.clearTimeout(metadataTimer)
  const codecNames =
    [props.video.media?.video_codec, props.video.media?.audio_codec].filter(Boolean).join(' / ') ||
    '不明なコーデック'
  playbackActionRequired.value = false
  playbackFailed.value = false
  playbackProgress.value = 0
  playbackMessage.value =
    name === 'remux'
      ? '再生用ファイルを準備しています… 0%'
      : `このブラウザでは再生できない形式です（${codecNames}）。H.264 に変換中です… 0%`
  try {
    const ready = await waitForConversion(
      name,
      () => sequence === prepareSequence,
      (percent) => {
        playbackProgress.value = percent
        playbackMessage.value =
          name === 'remux' ? `再生用ファイルを準備しています… ${percent}%` : `H.264 に変換中です… ${percent}%`
      },
    )
    if (!ready) return
    playbackMessage.value = ''
    playbackProgress.value = 0
    const position = videoEl.value?.currentTime || 0
    destroy()
    await nextTick()
    await makePlayer(name, position)
  } catch (cause) {
    if (sequence !== prepareSequence) return
    playbackProgress.value = 0
    playbackMessage.value = cause instanceof Error ? cause.message : '再生用ファイルを準備できません'
    playbackFailed.value = true
  }
}
async function convertInBackground(name: 'remux' | 'h264') {
  const label = name === 'remux' ? 'MP4 リマックス' : 'H.264 変換'
  const sequence = ++backgroundSequence
  conversionNote.value = `${label}を開始しました`
  try {
    const done = await waitForConversion(
      name,
      () => sequence === backgroundSequence,
      (percent) => {
        conversionNote.value = `${label}中… ${percent}%`
      },
    )
    if (!done) return
    conversionNote.value = `${label}が完了しました`
    emit('updated')
  } catch (cause) {
    if (sequence !== backgroundSequence) return
    conversionNote.value = cause instanceof Error ? cause.message : `${label}に失敗しました`
  }
}
function showPlaybackFailure() {
  window.clearTimeout(metadataTimer)
  playbackProgress.value = 0
  playbackActionRequired.value = false
  playbackMessage.value = '動画を読み込めません。別の形式を選ぶか、再試行してください。'
  playbackFailed.value = true
}
function retryPlayback() {
  playbackMessage.value = ''
  playbackFailed.value = false
  const position = videoEl.value?.currentTime || 0
  destroy()
  void nextTick().then(() => makePlayer(undefined, position))
}
async function retrieveChat() {
  busy.value = true
  try {
    await request(`/videos/${props.video.id}/chat/retrieve`, { method: 'POST', body: '{}' })
    void loadChat()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'チャットを取得できません'
  } finally {
    busy.value = false
  }
}
async function reparseChat() {
  busy.value = true
  try {
    await request(`/videos/${props.video.id}/chat/reparse`, { method: 'POST' })
    await loadChat()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'チャットを再解析できません'
  } finally {
    busy.value = false
  }
}
async function runTranscript() {
  busy.value = true
  try {
    await request(`/videos/${props.video.id}/transcript`, { method: 'POST', body: '{}' })
    await loadTranscript()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '文字起こしを開始できません'
  } finally {
    busy.value = false
  }
}
async function runSummary(mode: 'all' | 'final') {
  busy.value = true
  try {
    summary.value = await request(`/videos/${props.video.id}/summary?mode=${mode}`, {
      method: 'POST',
      body: '{}',
    })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '要約を作成できません'
  } finally {
    busy.value = false
  }
}
async function runVideoAction(path: string, method = 'POST') {
  if (method === 'POST' && (path === 'convert/remux' || path === 'convert/h264')) {
    const name = path === 'convert/remux' ? 'remux' : 'h264'
    if (player && !playbackFailed.value) await convertInBackground(name)
    else await prepareRendition(name)
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await request(`/videos/${props.video.id}/${path}`, { method, body: method === 'POST' ? '{}' : undefined })
    notice.value = '操作を受け付けました'
    emit('updated')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '操作に失敗しました'
  } finally {
    busy.value = false
  }
}
function destroy() {
  prepareSequence += 1
  window.clearTimeout(metadataTimer)
  window.clearInterval(chatTimer)
  window.clearInterval(transcriptTimer)
  transcriptTimer = 0
  window.clearInterval(playbackTimer)
  window.clearInterval(monitorTimer)
  hls?.destroy()
  hls = undefined
  if (hlsRetryTimer) clearTimeout(hlsRetryTimer)
  hlsRetryTimer = undefined
  playerSettings?.dispose()
  playerSettings = undefined
  player?.destroy()
  player = undefined
  videoEl.value = null
}
watch(
  [() => props.video.id, () => props.live],
  async () => {
    if (!initialized) return
    backgroundSequence += 1
    conversionNote.value = ''
    destroy()
    await nextTick()
    await makePlayer()
    void loadPanels()
  },
  { flush: 'post' },
)
watch(chatKind, () => {
  chatPage.value = 0
  followingChat.value = !chatKind.value && !chatQuery.value
  void loadChat()
  reloadDanmaku()
})
watch(includeDeleted, () => {
  chatPage.value = 0
  void loadChat()
  reloadDanmaku()
})
watch(quantity, () => reloadDanmaku())
watch([area, speed], () => reloadDanmaku())
function reloadDanmaku(seconds = playbackSeconds.value) {
  const range = danmakuRange(seconds)
  const requestKey = `${range.afterMs}:${range.beforeMs}:${chatKind.value}:${includeDeleted.value}`
  if (requestKey === requestedDanmakuKey) return
  try {
    player?.danmaku.reload({
      id: props.video.id,
      address: danmakuAddress(seconds),
      token: '',
      maximum:
        quantity.value === 'few'
          ? 35
          : quantity.value === 'many'
            ? 160
            : quantity.value === 'unlimited'
              ? 1000
              : 80,
      addition: [],
      user: 'viewer',
      bottom: area.value,
      unlimited: false,
      mutex: true,
      speedRate: 12 / speed.value,
    })
  } catch {
    /* player may be loading */
  }
}
function followChat() {
  followingChat.value = true
  chatQuery.value = ''
  chatKind.value = ''
  void loadChat()
}
watch(playbackSeconds, (value, previous) => {
  if (Math.abs(value - previous) > 3) {
    if (followingChat.value && !chatQuery.value && !chatKind.value) void loadChat()
  }
})
function onKey(event: KeyboardEvent) {
  const target = event.target
  const typing =
    target instanceof HTMLElement &&
    (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
  if (!typing) {
    const key = event.key.toLowerCase()
    if (key === 'd') danmakuOn.value = !danmakuOn.value
  }
  if (event.key === 'Escape') {
    const settingBox = mount.value?.querySelector<HTMLElement>('.dplayer-setting-box-open')
    if (settingBox) mount.value?.querySelector<HTMLElement>('.dplayer-mask')?.click()
    else emit('close')
  }
  if (event.key === 'Tab') {
    const root = mount.value?.closest('[role=dialog]')
    const nodes = [
      ...(root?.querySelectorAll<HTMLElement>('button,input,select,a[href],[tabindex="0"]') ?? []),
    ].filter((item) => !item.hasAttribute('disabled'))
    if (!nodes.length) return
    if (event.shiftKey && document.activeElement === nodes[0]) {
      event.preventDefault()
      nodes[nodes.length - 1]?.focus()
    } else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) {
      event.preventDefault()
      nodes[0]?.focus()
    }
  }
}
watch(
  () => props.video.id,
  () => {
    focusBefore = document.activeElement instanceof HTMLElement ? document.activeElement : null
  },
)
onMounted(() => {
  document.body.classList.add('dialog-open')
  document.addEventListener('keydown', onKey)
  focusBefore = document.activeElement instanceof HTMLElement ? document.activeElement : null
  initialized = true
  void makePlayer()
  void loadPanels()
})
onBeforeUnmount(() => {
  document.body.classList.remove('dialog-open')
  document.removeEventListener('keydown', onKey)
  backgroundSequence += 1
  destroy()
  focusBefore?.focus()
})
</script>
<template>
  <div class="dialog-backdrop player-backdrop" @mousedown.self="emit('close')">
    <section class="player-dialog" role="dialog" aria-modal="true" :aria-label="`再生: ${video.title}`">
      <PlayerHeader :video="video" :live="live" @close="emit('close')" />
      <div class="player-layout">
        <div class="player-main">
          <div class="player-stage">
            <div
              ref="mount"
              class="dplayer-mount"
              :class="{ 'danmaku-off': !danmakuOn }"
              :style="{
                '--danmaku-opacity': opacity,
                '--danmaku-area': `${area}%`,
                '--danmaku-size': `${font}px`,
              }"
            ></div>
            <div v-if="playbackMessage" class="playback-status">
              <p role="status">{{ playbackMessage }}</p>
              <progress v-if="playbackProgress > 0" :value="playbackProgress" max="100"></progress>
              <div v-if="playbackActionRequired" class="playback-actions">
                <button class="button primary small" @click="prepareRendition('h264')">変換して再生</button>
              </div>
              <div v-else-if="playbackFailed" class="playback-actions">
                <button class="button secondary small" @click="prepareRendition('remux')">
                  別の形式で再生
                </button>
                <button class="button primary small" @click="prepareRendition('h264')">変換して再生</button>
                <a class="button secondary small" :href="apiUrl(`/videos/${video.id}/file?download=true`)">
                  ダウンロード
                </a>
                <button class="button secondary small" @click="retryPlayback">再試行</button>
              </div>
            </div>
          </div>
          <PlayerVideoInfo
            class="player-desktop-info"
            :video="video"
            :busy="busy"
            :note="conversionNote"
            @action="runVideoAction"
          />
        </div>
        <PlayerPanels
          v-model:tab="tab"
          v-model:sync-transcript="syncTranscript"
          v-model:chat-query="chatQuery"
          v-model:chat-kind="chatKind"
          v-model:include-deleted="includeDeleted"
          v-model:transcript-query="transcriptQuery"
          :chat="chat"
          :video="video"
          :transcript="transcript"
          :timeline="timeline"
          :summary="summary"
          :error="error"
          :busy="busy"
          :video-id="video.id"
          :chat-page="chatPage"
          :transcript-page="transcriptPage"
          :transcript-sync-error="transcriptSyncError"
          :playback-seconds="playbackSeconds"
          :following="followingChat"
          :capture-frame="captureCurrentFrame"
          :storage-name="video.storage_name || ''"
          :conversion-note="conversionNote"
          @search-chat="searchChat"
          @retrieve-chat="retrieveChat"
          @seek-covered="seek"
          @reparse-chat="reparseChat"
          @next-chat-page="nextChatPage"
          @previous-chat-page="previousChatPage"
          @run-transcript="runTranscript"
          @search-transcript="searchTranscript"
          @next-transcript-page="nextTranscriptPage"
          @sync-transcript="syncTranscriptAt"
          @run-summary="runSummary"
          @seek="seek"
          @follow-chat="followChat"
          @chat-scrolled="followingChat = false"
          @video-action="runVideoAction"
        />
      </div>
    </section>
  </div>
</template>
