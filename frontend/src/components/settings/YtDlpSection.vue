<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { request } from '../../api/client'

type Channel = 'stable' | 'nightly' | 'master'
interface YtDlpState {
  channel: Channel
  auto_update: boolean
  version: string | null
  latest_version: string | null
  checked_at: string | null
  update_available: boolean | null
  update_in_progress: boolean
  update_phase: string
  update_error: string | null
  deno_available: boolean
  deno_version: string | null
  deno_supported: boolean
  ejs_available: boolean
  managed: boolean
  managed_update_supported: boolean
}

const state = ref<YtDlpState | null>(null)
const channel = ref<Channel>('nightly')
const autoUpdate = ref(true)
const loading = ref(true)
const busy = ref<'save' | 'update' | 'force' | null>(null)
const error = ref('')
const notice = ref('')
let pollTimer: number | undefined

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    const result = await request<YtDlpState>('/settings/ytdlp')
    state.value = result
    channel.value = result.channel
    autoUpdate.value = result.auto_update
    scheduleRefresh(result.update_in_progress)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'yt-dlp 設定を読み込めませんでした'
  } finally {
    loading.value = false
  }
}
async function refreshStatus() {
  try {
    const result = await request<YtDlpState>('/settings/ytdlp')
    state.value = result
    scheduleRefresh(result.update_in_progress)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'yt-dlp の状態を更新できませんでした'
  }
}
function scheduleRefresh(active: boolean) {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  pollTimer = undefined
  if (active) pollTimer = window.setTimeout(() => void refreshStatus(), 2000)
}
async function saveSettings() {
  busy.value = 'save'
  error.value = ''
  notice.value = ''
  try {
    const result = await request<YtDlpState>('/settings/ytdlp', {
      method: 'PUT',
      body: JSON.stringify({ channel: channel.value, auto_update: autoUpdate.value }),
    })
    state.value = result
    channel.value = result.channel
    autoUpdate.value = result.auto_update
    notice.value = 'yt-dlp の設定を保存しました'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'yt-dlp 設定を保存できませんでした'
  } finally {
    busy.value = null
  }
}
async function update(force: boolean) {
  busy.value = force ? 'force' : 'update'
  error.value = ''
  notice.value = ''
  try {
    await request<{ started: boolean; force: boolean }>('/settings/ytdlp/update', {
      method: 'POST',
      body: JSON.stringify({ force }),
    })
    notice.value = '更新処理を開始しました。録画中の処理が終わってから切り替えます'
    await refreshStatus()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'yt-dlp の更新を開始できませんでした'
  } finally {
    busy.value = null
  }
}
function checkedAt(value: string | null | undefined) {
  if (!value) return '未確認'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ja-JP')
}
function phaseLabel(phase: string | undefined) {
  if (phase === 'waiting_for_recordings') return '録画・ダウンロードの完了待ち'
  if (phase === 'downloading_and_verifying') return '取得ファイルを検証中'
  if (phase === 'completed') return '更新しました'
  if (phase === 'up_to_date') return '最新の版です'
  if (phase === 'failed') return '更新に失敗しました'
  return '待機中'
}

onMounted(loadSettings)
onBeforeUnmount(() => {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
})
</script>

<template>
  <section class="card ytdlp-settings">
    <h2>yt-dlp の更新</h2>
    <p>
      YouTube の仕様変更に追従するため、nightly
      を推奨します。自動更新は24時間に一度確認し、録画やダウンロードが終わってから新しい実行ファイルへ切り替えます。
    </p>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="status-success" role="status">{{ notice }}</p>
    <p v-if="loading" role="status">更新設定を読み込み中…</p>

    <div v-if="state" class="ytdlp-version-summary" aria-live="polite">
      <p><strong>使用中</strong> {{ state.version || '不明' }}</p>
      <p><strong>選択チャンネルの最新版</strong> {{ state.latest_version || '未確認' }}</p>
      <p><strong>最終確認</strong> {{ checkedAt(state.checked_at) }}</p>
      <p v-if="state.update_in_progress" role="status">更新処理: {{ phaseLabel(state.update_phase) }}</p>
      <p v-else-if="state.update_available === true" class="ytdlp-warning" role="status">
        新しい版があります。手動更新で今すぐ適用できます。
      </p>
      <p v-else-if="state.update_available === false" role="status">選択チャンネルの最新の版です。</p>
      <p v-if="state.managed_update_supported === false" class="ytdlp-warning" role="status">
        独自の YT_DLP_PATH が設定されているため、アプリからの更新は無効です。既定値の yt-dlp
        に戻してください。
      </p>
    </div>

    <div class="form-grid ytdlp-preferences">
      <label class="field">
        更新チャンネル
        <select v-model="channel" :disabled="loading || busy !== null">
          <option value="stable">stable</option>
          <option value="nightly">nightly（推奨）</option>
          <option value="master">master（最新・変更が多い）</option>
        </select>
      </label>
      <label class="field ytdlp-auto-update">
        <span>自動更新</span>
        <span>
          <input v-model="autoUpdate" type="checkbox" :disabled="loading || busy !== null" />
          新しい版を自動で適用
        </span>
      </label>
    </div>
    <div class="settings-actions">
      <button
        class="button secondary"
        type="button"
        :disabled="loading || busy !== null"
        @click="saveSettings"
      >
        {{ busy === 'save' ? '保存中…' : '設定を保存' }}
      </button>
      <button
        class="button primary"
        type="button"
        :disabled="
          loading || busy !== null || state?.update_in_progress || state?.managed_update_supported === false
        "
        @click="update(false)"
      >
        {{ busy === 'update' ? '更新を開始中…' : '今すぐ更新' }}
      </button>
      <button
        class="button secondary"
        type="button"
        :disabled="
          loading || busy !== null || state?.update_in_progress || state?.managed_update_supported === false
        "
        @click="update(true)"
      >
        {{ busy === 'force' ? '再インストール中…' : '同じ版を強制再インストール' }}
      </button>
    </div>
    <p v-if="state?.update_error" class="inline-error" role="alert">
      yt-dlp 更新エラー: {{ state.update_error }}
    </p>

    <div v-if="state" class="ytdlp-dependencies">
      <h3>YouTube取得の依存関係</h3>
      <p v-if="state.deno_supported">Deno {{ state.deno_version }} を利用できます。</p>
      <p v-else-if="state.deno_available" class="ytdlp-warning">
        Deno {{ state.deno_version || '不明' }} は未対応です。Deno 2.3.0 以降へ更新してください。
      </p>
      <p v-else class="ytdlp-warning">
        Deno が見つかりません。YouTubeの完全な取得にはDeno 2.3.0以降が必要です。
      </p>
      <p>{{ state.ejs_available ? 'yt-dlp-ejs を確認済み' : 'yt-dlp-ejs が見つかりません' }}</p>
      <a
        href="https://docs.deno.com/runtime/getting_started/installation/"
        target="_blank"
        rel="noopener noreferrer"
      >
        Denoのインストール手順
      </a>
      <p>Denoをインストールした後は、サービスの再起動後に検出状態が更新されます。</p>
      <p>
        Bot判定やログイン要求が続く場合、更新だけでは解消しないことがあります。対象チャンネルのCookie
        Profileを確認してください。Cookieをログや共有資料へ貼り付けないでください。
      </p>
    </div>
  </section>
</template>

<style scoped>
.ytdlp-version-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
  margin: 16px 0;
}

.ytdlp-version-summary p,
.ytdlp-dependencies p {
  margin: 0;
  overflow-wrap: anywhere;
}

.ytdlp-settings .settings-actions {
  justify-content: flex-start;
  flex-wrap: wrap;
}

.ytdlp-auto-update > span:last-child {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ytdlp-auto-update input[type='checkbox'] {
  width: auto;
  height: auto;
  margin: 0;
}

.ytdlp-dependencies {
  display: grid;
  gap: 10px;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.ytdlp-dependencies h3 {
  margin: 0;
}

.ytdlp-warning {
  color: var(--warning);
}

@media (max-width: 600px) {
  .ytdlp-version-summary {
    grid-template-columns: minmax(0, 1fr);
  }

  .ytdlp-settings .settings-actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
