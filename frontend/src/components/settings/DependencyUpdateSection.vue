<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { request } from '../../api/client'

interface DependencyUpdateState {
  phase: string
  started_at: string | null
  finished_at: string | null
  message: string | null
  error: string | null
  supported: boolean
  unsupported_reason: string | null
  python_version: string
  pip_version: string | null
  cuda_dependencies: boolean
  updating: boolean
  yt_dlp?: {
    update_in_progress?: boolean
    version?: string | null
    latest_version?: string | null
    update_available?: boolean | null
    checked_at?: string | null
    update_error?: string | null
    managed_update_supported?: boolean
  }
}

const state = ref<DependencyUpdateState | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
let pollTimer: number | undefined

function phaseLabel(phase: string) {
  const labels: Record<string, string> = {
    preparing: '依存関係を準備中',
    waiting_for_idle: '録画・文字起こし・変換の完了待ち',
    switching: 'サービスを切り替えて起動確認中',
    rolling_back: '旧環境へ復旧中',
    completed: '更新しました',
    rolled_back: '更新に失敗し、旧環境へ戻しました',
    rollback_failed: '更新と復旧に失敗しました。サービス状態を確認してください',
    failed: '更新に失敗しました',
    cancelled: '更新をキャンセルしました',
  }
  return labels[phase] || '更新待ち'
}

function scheduleRefresh(active: boolean) {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  pollTimer = undefined
  if (active) pollTimer = window.setTimeout(() => void loadStatus(), 3000)
}

async function loadStatus() {
  try {
    state.value = await request<DependencyUpdateState>('/settings/dependencies')
    error.value = ''
    scheduleRefresh(state.value.updating)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '更新状態を取得できませんでした'
    if (state.value?.updating) scheduleRefresh(true)
  } finally {
    loading.value = false
  }
}

async function update() {
  busy.value = true
  error.value = ''
  try {
    await request('/settings/dependencies/update', { method: 'POST', body: '{}' })
    await loadStatus()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '更新を開始できませんでした'
  } finally {
    busy.value = false
  }
}

async function cancel() {
  busy.value = true
  error.value = ''
  try {
    await request('/settings/dependencies/cancel', { method: 'POST', body: '{}' })
    await loadStatus()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '更新をキャンセルできませんでした'
  } finally {
    busy.value = false
  }
}

onMounted(loadStatus)
onBeforeUnmount(() => {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
})
</script>

<template>
  <section class="card dependency-update-settings">
    <h2>依存関係の一括更新</h2>
    <p>
      Pythonパッケージとnpmパッケージを更新します。固定バージョンのライブラリは維持し、バージョン範囲が指定されたものを範囲内の最新版へ更新します。アプリ管理のyt-dlpは設定中のチャンネルで更新します。
    </p>
    <p>新しい環境を準備してから、録画・文字起こし・変換が終わった時点で切り替えます。起動確認に失敗した場合は旧環境へ戻します。</p>
    <p class="meta">
      Python・Node.js本体、CUDA用ライブラリの固定版、システム全体に入れたDeno・ffmpeg、PostgreSQL本体は、この操作の対象外です。
    </p>

    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">更新状態を読み込み中…</p>
    <template v-if="state">
      <p v-if="state.updating" class="dependency-update-status" role="status" aria-live="polite">
        {{ state.message || phaseLabel(state.phase) }}
      </p>
      <p v-else-if="['completed', 'rolled_back', 'rollback_failed', 'failed', 'cancelled'].includes(state.phase)"
         class="dependency-update-status" role="status">
        {{ phaseLabel(state.phase) }}
      </p>
      <p v-if="state.error" class="inline-error" role="alert">{{ state.error }}</p>
      <dl class="dependency-update-meta">
        <div><dt>Python</dt><dd>{{ state.python_version }}</dd></div>
        <div><dt>pip</dt><dd>{{ state.pip_version || '不明' }}</dd></div>
        <div><dt>CUDA構成</dt><dd>{{ state.cuda_dependencies ? '導入済み・固定版を維持' : '未導入' }}</dd></div>
        <div v-if="state.yt_dlp"><dt>yt-dlp</dt><dd>{{ state.yt_dlp.version || '確認中' }}<template v-if="state.yt_dlp.latest_version"> → {{ state.yt_dlp.latest_version }}</template></dd></div>
      </dl>
      <p v-if="state.yt_dlp?.update_error" class="inline-error" role="status">yt-dlp更新: {{ state.yt_dlp.update_error }}</p>
      <p v-if="state.yt_dlp?.managed_update_supported === false" class="ytdlp-warning" role="status">
        独自のyt-dlpパスが設定されているため、yt-dlpは一括更新の対象外です。
      </p>
      <p v-if="!state.supported" class="ytdlp-warning" role="status">
        {{ state.unsupported_reason || 'この環境では依存関係を更新できません' }}
      </p>
      <div class="settings-actions">
        <button class="button primary" type="button"
                :disabled="loading || busy || state.updating || !state.supported" @click="update">
          {{ busy ? '処理中…' : '管理対象をまとめて更新' }}
        </button>
        <button v-if="state.phase === 'preparing' || state.phase === 'waiting_for_idle'"
                class="button secondary" type="button" :disabled="busy" @click="cancel">
          更新をキャンセル
        </button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.dependency-update-settings > p {
  max-width: 76ch;
}
.dependency-update-status {
  color: var(--text);
  font-weight: 600;
}
.dependency-update-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr));
  gap: 8px 20px;
  margin: 16px 0;
}
.dependency-update-meta div {
  display: flex;
  gap: 8px;
  min-width: 0;
}
.dependency-update-meta dt {
  color: var(--muted);
}
.dependency-update-meta dd {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
