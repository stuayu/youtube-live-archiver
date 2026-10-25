<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { jsonBody, request } from '../../api/client'

interface SocialCapture {
  data_base64: string
  mime_type: 'image/jpeg'
  width: number
  height: number
  playback_seconds: number
}
interface SelectedImage extends SocialCapture {
  id: number
  alt: string
  playback_seconds: number
}
interface SocialSettings {
  bluesky: { configured: boolean; identifier: string; service_url: string }
  misskey: { configured: boolean; instance_url: string }
}
interface SocialItem {
  provider: 'bluesky' | 'misskey'
  id: string
  author: string
  handle: string
  text: string
  created_at: string
  url: string | null
  images: { url: string; alt: string }[]
}
interface SearchResponse {
  items: SocialItem[]
  providers: Record<string, { configured: boolean; error: string | null; cursor: string | null }>
}
interface PostResponse {
  results: Record<string, { success: boolean; url?: string | null; error?: string }>
}

const props = defineProps<{
  videoId: string
  youtubeVideoId: string
  playbackSeconds: number
  captureFrame?: () => Promise<SocialCapture>
}>()

const mode = ref<'search' | 'post'>('search')
const settings = ref<SocialSettings | null>(null)
const settingsError = ref('')
const tag = ref('')
const query = ref('')
const searchTargets = ref({ bluesky: true, misskey: true })
const postTargets = ref({ bluesky: false, misskey: false })
const providerState = ref<SearchResponse['providers']>({})
const results = ref<SocialItem[]>([])
const searchBusy = ref(false)
const searchError = ref('')
const cursors = ref({ bluesky: null as string | null, misskey: null as string | null })
const recentTags = ref<string[]>([])
const text = ref('')
const hashtags = ref('')
const includeVideoLink = ref(false)
const visibility = ref<'public' | 'home' | 'followers'>('public')
const captures = ref<SelectedImage[]>([])
const captureBusy = ref(false)
const captureError = ref('')
const postBusy = ref(false)
const postError = ref('')
const postResults = ref<PostResponse['results']>({})

const connectedCount = computed(
  () =>
    Number(Boolean(settings.value?.bluesky.configured)) + Number(Boolean(settings.value?.misskey.configured)),
)
const captureLimitReached = computed(() => captures.value.length >= 4)
const tagList = computed(() => [
  ...new Set(
    hashtags.value
      .split(/[\s,，]+/)
      .map((value) => value.trim().replace(/^#+/, ''))
      .filter(Boolean),
  ),
])
const hashtagsValid = computed(
  () => tagList.value.length <= 10 && tagList.value.every((item) => /^[\p{L}\p{N}_]{1,64}$/u.test(item)),
)
const hashtagError = computed(() => {
  if (!tagList.value.length) return ''
  if (tagList.value.length > 10) return 'ハッシュタグは10個までです'
  if (!hashtagsValid.value) return '各ハッシュタグは英数字・アンダースコアで64文字以内にしてください'
  return ''
})
const postPreview = computed(() => {
  let value = text.value.trim()
  const existingTags = new Set(
    [...value.matchAll(/(^|[^\p{L}\p{N}_])#([\p{L}\p{N}_]{1,64})(?![\p{L}\p{N}_])/gu)].map(
      (match) => match[2]?.toLocaleLowerCase() || '',
    ),
  )
  for (const item of tagList.value) {
    if (!existingTags.has(item.toLocaleLowerCase())) {
      value += `${value ? '\n' : ''}#${item}`
      existingTags.add(item.toLocaleLowerCase())
    }
  }
  if (includeVideoLink.value)
    value += `${value ? '\n' : ''}https://www.youtube.com/watch?v=${props.youtubeVideoId}`
  return value
})
const postLength = computed(() => Array.from(postPreview.value).length)
const selectedPostTargets = computed(() =>
  (['bluesky', 'misskey'] as const).filter((provider) => postTargets.value[provider]),
)
const hasMoreResults = computed(
  () =>
    query.value.trim().replace(/^#+/, '') === tag.value &&
    ((searchTargets.value.bluesky && Boolean(cursors.value.bluesky)) ||
      (searchTargets.value.misskey && Boolean(cursors.value.misskey))),
)
const canPost = computed(
  () =>
    !postBusy.value &&
    connectedCount.value > 0 &&
    selectedPostTargets.value.length > 0 &&
    hashtagsValid.value &&
    postLength.value <= 300 &&
    Boolean(postPreview.value || captures.value.length),
)

function providerLabel(provider: string) {
  return provider === 'bluesky' ? 'Bluesky' : 'Misskey'
}
function formatTime(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(safe / 3600)
  const minutes = Math.floor((safe % 3600) / 60)
  const remaining = safe % 60
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
    : `${minutes}:${String(remaining).padStart(2, '0')}`
}
function rememberTag(value: string) {
  const normalized = value.trim().replace(/^#+/, '')
  if (!normalized) return
  recentTags.value = [normalized, ...recentTags.value.filter((item) => item !== normalized)].slice(0, 6)
  try {
    localStorage.setItem('yla-social-recent-tags', JSON.stringify(recentTags.value))
  } catch {
    // Recent searches are a convenience and can be unavailable in private browsing.
  }
}
function loadRecentTags() {
  try {
    const stored = JSON.parse(localStorage.getItem('yla-social-recent-tags') || '[]')
    if (Array.isArray(stored))
      recentTags.value = stored.filter((item) => typeof item === 'string').slice(0, 6)
  } catch {
    recentTags.value = []
  }
}
async function loadSettings() {
  settingsError.value = ''
  try {
    settings.value = await request<SocialSettings>('/settings/social')
  } catch (error) {
    settingsError.value = error instanceof Error ? error.message : 'SNS 設定を読み込めませんでした'
  }
}
function selectedSearchProviders() {
  return (['bluesky', 'misskey'] as const).filter((provider) => searchTargets.value[provider])
}
async function searchSocial(loadMore = false) {
  const normalized = query.value.trim().replace(/^#+/, '')
  if (!normalized) {
    searchError.value = 'ハッシュタグを入力してください'
    return
  }
  if (!loadMore) {
    tag.value = normalized
    rememberTag(normalized)
    results.value = []
    cursors.value = { bluesky: null, misskey: null }
    providerState.value = {}
  }
  const providers = selectedSearchProviders().filter(
    (provider) => !loadMore || Boolean(cursors.value[provider]),
  )
  if (!providers.length) {
    searchError.value = '検索先を1つ以上選択してください'
    return
  }
  searchBusy.value = true
  searchError.value = ''
  try {
    const params = new URLSearchParams({ tag: normalized, providers: providers.join(',') })
    if (loadMore && cursors.value.bluesky) params.set('bluesky_cursor', cursors.value.bluesky)
    if (loadMore && cursors.value.misskey) params.set('misskey_cursor', cursors.value.misskey)
    const response = await request<SearchResponse>(`/social/search?${params.toString()}`)
    providerState.value = { ...providerState.value, ...response.providers }
    for (const provider of providers) {
      cursors.value[provider] = response.providers[provider]?.cursor || null
    }
    const known = new Set(results.value.map((item) => `${item.provider}:${item.id}`))
    results.value = [
      ...results.value,
      ...response.items.filter((item) => !known.has(`${item.provider}:${item.id}`)),
    ].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    if (
      response.items.length === 0 &&
      !loadMore &&
      providers.every((item) => response.providers[item]?.error)
    ) {
      searchError.value = '検索できるサービスがありません。接続設定と権限を確認してください'
    }
  } catch (error) {
    searchError.value = error instanceof Error ? error.message : '検索に失敗しました'
  } finally {
    searchBusy.value = false
  }
}
function chooseRecent(value: string) {
  query.value = value
  void searchSocial()
}
function postFromTag() {
  hashtags.value = tag.value || query.value.replace(/^#+/, '')
  mode.value = 'post'
}
function updatePostTarget(provider: 'bluesky' | 'misskey', checked: boolean) {
  postTargets.value[provider] = checked
  postResults.value = {}
  postError.value = ''
}
function clearPostResults() {
  postResults.value = {}
  postError.value = ''
}
async function addCapture() {
  if (!props.captureFrame || captureBusy.value || captureLimitReached.value) return
  captureBusy.value = true
  captureError.value = ''
  try {
    const frame = await props.captureFrame()
    const capturedAt = frame.playback_seconds
    captures.value.push({
      ...frame,
      id: Date.now() + Math.floor(Math.random() * 1000),
      alt: `動画のキャプチャ ${formatTime(capturedAt)}`,
    })
    clearPostResults()
  } catch (error) {
    captureError.value = error instanceof Error ? error.message : '映像をキャプチャできませんでした'
  } finally {
    captureBusy.value = false
  }
}
function removeCapture(id: number) {
  captures.value = captures.value.filter((item) => item.id !== id)
  clearPostResults()
}
function clearCaptures() {
  captures.value = []
  clearPostResults()
}
async function submitPost() {
  if (!canPost.value) return
  postBusy.value = true
  postError.value = ''
  postResults.value = {}
  try {
    const response = await request<PostResponse>(
      `/videos/${props.videoId}/social/post`,
      jsonBody({
        targets: selectedPostTargets.value,
        text: text.value,
        hashtags: tagList.value,
        include_video_link: includeVideoLink.value,
        visibility: visibility.value,
        images: captures.value.map((image) => ({
          data_base64: image.data_base64,
          mime_type: image.mime_type,
          width: image.width,
          height: image.height,
          playback_seconds: image.playback_seconds,
          alt: image.alt,
        })),
      }),
    )
    postResults.value = response.results
    for (const [provider, result] of Object.entries(response.results)) {
      if (result.success && (provider === 'bluesky' || provider === 'misskey')) {
        postTargets.value[provider] = false
      }
    }
    const failures = Object.values(response.results).filter((result) => !result.success)
    if (failures.length && Object.values(response.results).some((result) => result.success)) {
      postError.value = '成功した投稿先は選択解除しました。失敗した投稿先だけ再試行できます'
    } else if (failures.length) {
      postError.value = '投稿できませんでした。設定と接続状態を確認してください'
    } else {
      captures.value = []
      text.value = ''
      hashtags.value = ''
      includeVideoLink.value = false
    }
  } catch (error) {
    postError.value = error instanceof Error ? error.message : '投稿に失敗しました'
  } finally {
    postBusy.value = false
  }
}

onMounted(() => {
  loadRecentTags()
  void loadSettings()
})
</script>

<template>
  <section class="social-timeline" aria-label="SNS タイムライン">
    <div class="social-mode-tabs" role="tablist" aria-label="SNS の操作">
      <button
        id="social-search-tab"
        type="button"
        role="tab"
        :aria-selected="mode === 'search'"
        :class="{ active: mode === 'search' }"
        @click="mode = 'search'"
      >
        検索
      </button>
      <button
        id="social-post-tab"
        type="button"
        role="tab"
        :aria-selected="mode === 'post'"
        :class="{ active: mode === 'post' }"
        @click="mode = 'post'"
      >
        投稿
      </button>
    </div>

    <template v-if="mode === 'search'">
      <form class="social-search-form" @submit.prevent="searchSocial()">
        <label class="field">
          ハッシュタグ
          <input v-model="query" maxlength="65" placeholder="#配信名" autocomplete="off" />
        </label>
        <fieldset class="social-provider-list">
          <legend>検索先</legend>
          <label><input v-model="searchTargets.bluesky" type="checkbox" /> Bluesky</label>
          <label><input v-model="searchTargets.misskey" type="checkbox" /> Misskey</label>
        </fieldset>
        <button class="button primary" type="submit" :disabled="searchBusy">
          {{ searchBusy ? '検索中…' : '検索' }}
        </button>
      </form>
      <div v-if="recentTags.length" class="social-recent-tags" aria-label="最近使ったハッシュタグ">
        <span>最近:</span>
        <button v-for="recent in recentTags" :key="recent" type="button" @click="chooseRecent(recent)">
          #{{ recent }}
        </button>
      </div>
      <div v-if="tag" class="social-search-heading">
        <strong>#{{ tag }}</strong>
        <button class="button secondary small" type="button" @click="postFromTag">このタグで投稿</button>
      </div>
      <p v-if="searchError" class="inline-error" role="alert">{{ searchError }}</p>
      <p v-if="settingsError" class="inline-error" role="alert">{{ settingsError }}</p>
      <div class="social-provider-status" aria-live="polite">
        <p
          v-for="(state, provider) in providerState"
          :key="provider"
          :class="{ 'inline-error': state.error }"
        >
          {{ providerLabel(provider) }}: {{ state.error || (state.configured ? '検索済み' : '未接続') }}
        </p>
      </div>
      <ol v-if="results.length" class="social-results">
        <li v-for="item in results" :key="`${item.provider}:${item.id}`" class="social-result">
          <header>
            <span class="social-provider-badge" :class="item.provider">{{
              providerLabel(item.provider)
            }}</span>
            <strong>{{ item.author || item.handle }}</strong>
            <time v-if="item.created_at" :datetime="item.created_at">
              {{
                new Date(item.created_at).toLocaleString('ja-JP', { dateStyle: 'short', timeStyle: 'short' })
              }}
            </time>
          </header>
          <p class="social-result-text">{{ item.text }}</p>
          <div v-if="item.images.length" class="social-result-images">
            <img
              v-for="image in item.images.slice(0, 4)"
              :key="image.url"
              :src="image.url"
              :alt="image.alt"
              loading="lazy"
              referrerpolicy="no-referrer"
            />
          </div>
          <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer">投稿を開く</a>
        </li>
      </ol>
      <p v-else-if="tag && !searchBusy && !searchError" class="empty-state">
        結果はありません。タグを確認するか、検索先を変更してください。
      </p>
      <button
        v-if="hasMoreResults"
        class="button secondary"
        type="button"
        :disabled="searchBusy"
        @click="searchSocial(true)"
      >
        {{ searchBusy ? '読み込み中…' : 'さらに読み込む' }}
      </button>
    </template>

    <form v-else class="social-post-form" @submit.prevent="submitPost">
      <p v-if="!connectedCount" class="social-setup-help">
        投稿するには「設定」の SNS 連携で Bluesky または Misskey を接続してください。
      </p>
      <fieldset class="social-provider-list">
        <legend>投稿先</legend>
        <label>
          <input
            type="checkbox"
            :checked="postTargets.bluesky"
            :disabled="!settings?.bluesky.configured || postBusy"
            @change="updatePostTarget('bluesky', ($event.target as HTMLInputElement).checked)"
          />
          Bluesky
          <span v-if="settings?.bluesky.configured" class="social-account">{{
            settings.bluesky.identifier
          }}</span>
          <span v-else class="social-disconnected">未接続</span>
        </label>
        <label>
          <input
            type="checkbox"
            :checked="postTargets.misskey"
            :disabled="!settings?.misskey.configured || postBusy"
            @change="updatePostTarget('misskey', ($event.target as HTMLInputElement).checked)"
          />
          Misskey
          <span v-if="settings?.misskey.configured" class="social-account">{{
            settings.misskey.instance_url
          }}</span>
          <span v-else class="social-disconnected">未接続</span>
        </label>
      </fieldset>
      <label class="field">
        本文
        <textarea
          v-model="text"
          rows="3"
          maxlength="3000"
          placeholder="配信の見どころを共有"
          @input="clearPostResults"
        />
      </label>
      <label class="field">
        ハッシュタグ
        <input v-model="hashtags" maxlength="700" placeholder="配信名 今日の配信" @input="clearPostResults" />
        <small>空白またはカンマで区切ってください。# は自動で付きます。</small>
        <small v-if="hashtagError" class="inline-error" role="alert">{{ hashtagError }}</small>
      </label>
      <div class="social-post-options">
        <label class="check-field">
          <input v-model="includeVideoLink" type="checkbox" @change="clearPostResults" />
          YouTube の動画リンクを含める
        </label>
        <label class="field">
          Misskey の公開範囲
          <select v-model="visibility" @change="clearPostResults">
            <option value="public">公開</option>
            <option value="home">ホーム</option>
            <option value="followers">フォロワー限定</option>
          </select>
        </label>
      </div>
      <section class="social-captures" aria-labelledby="capture-heading">
        <div class="social-capture-heading">
          <div>
            <h3 id="capture-heading">再生画面のキャプチャ</h3>
            <p>映像のみを保存します。投稿前に確認できます（{{ captures.length }}/4 枚）。</p>
          </div>
          <button
            class="button secondary"
            type="button"
            :disabled="captureBusy || captureLimitReached || !captureFrame"
            @click="addCapture"
          >
            {{ captureBusy ? '取得中…' : 'この場面を追加' }}
          </button>
        </div>
        <p v-if="captureError" class="inline-error" role="alert">{{ captureError }}</p>
        <ul v-if="captures.length" class="social-capture-list">
          <li v-for="capture in captures" :key="capture.id" class="social-capture-item">
            <img :src="`data:image/jpeg;base64,${capture.data_base64}`" :alt="capture.alt" />
            <div class="social-capture-details">
              <strong>{{ formatTime(capture.playback_seconds) }}</strong>
              <label class="field">
                代替テキスト
                <input v-model="capture.alt" maxlength="1000" @input="clearPostResults" />
              </label>
              <button class="button secondary small" type="button" @click="removeCapture(capture.id)">
                この画像を削除
              </button>
            </div>
          </li>
        </ul>
        <button v-if="captures.length > 1" class="social-clear-captures" type="button" @click="clearCaptures">
          画像をすべて削除
        </button>
      </section>
      <div class="social-post-preview">
        <div class="social-preview-heading">
          <strong>投稿プレビュー</strong>
          <span :class="{ 'inline-error': postLength > 300 }">{{ postLength }}/300</span>
        </div>
        <p>{{ postPreview || '本文やキャプチャは投稿前にここで確認できます。' }}</p>
        <small v-if="includeVideoLink">リンク: youtube.com/watch?v={{ youtubeVideoId }}</small>
      </div>
      <p
        v-if="postError"
        :class="{ 'inline-error': !Object.values(postResults).some((result) => result.success) }"
        role="status"
      >
        {{ postError }}
      </p>
      <ul v-if="Object.keys(postResults).length" class="social-post-results" aria-live="polite">
        <li v-for="(result, provider) in postResults" :key="provider">
          <span :class="result.success ? 'social-success' : 'inline-error'">
            {{ providerLabel(provider) }}: {{ result.success ? '投稿しました' : result.error }}
          </span>
          <a v-if="result.success && result.url" :href="result.url" target="_blank" rel="noopener noreferrer"
            >投稿を開く</a
          >
        </li>
      </ul>
      <button class="button primary social-submit" type="submit" :disabled="!canPost">
        {{ postBusy ? '投稿中…' : '選択した先に投稿' }}
      </button>
      <p class="social-private-note">
        動画リンクは初期状態で含まれません。投稿先を選んで送信するまで、キャプチャ画像は外部サービスへ送信されません。
      </p>
    </form>
  </section>
</template>
