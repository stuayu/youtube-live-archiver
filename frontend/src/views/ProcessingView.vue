<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/resources'
import type { ProcessingJob, ProcessingJobDetail, ProcessingJobPage } from '../api/types'
import { useWorkspaceContext } from '../composables/workspaceContext'
import { formatDate } from '../lib/format'

const ctx = useWorkspaceContext()
const page = ref<ProcessingJobPage | null>(null)
const details = ref<Record<string, ProcessingJobDetail>>({})
const detailErrors = ref<Record<string, string>>({})
const expanded = ref(new Set<string>())
const kind = ref('')
const status = ref('')
const search = ref('')
const appliedSearch = ref('')
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const lastUpdated = ref('')
const pageSize = 30
let requestGeneration = 0
let searchTimer = 0
let pollTimer = 0

const kinds = [
  ['TRANSCRIBE', '文字起こし'],
  ['SUMMARIZE_CHUNKS', '区間要約'],
  ['SUMMARIZE_FINAL', '最終要約'],
  ['CHAT_REPLAY', 'チャット取得'],
] as const
const statuses = [
  ['', 'すべて'],
  ['processing', '処理中'],
  ['pending', '待機中'],
  ['retry_wait', 'リトライ待ち'],
  ['failed', '失敗'],
  ['completed', '完了'],
  ['disabled', '無効'],
] as const
const visibleJobs = computed(() => page.value?.items ?? [])
const pageStart = computed(() => (page.value?.total ? offset.value + 1 : 0))
const pageEnd = computed(() => Math.min(offset.value + visibleJobs.value.length, page.value?.total ?? 0))
const pageCount = computed(() => Math.max(1, Math.ceil((page.value?.total ?? 0) / pageSize)))
const pageNumber = computed(() => Math.floor(offset.value / pageSize) + 1)

function kindLabel(value: string) {
  return kinds.find(([key]) => key === value)?.[1] ?? value
}

function statusLabel(value: string) {
  return statuses.find(([key]) => key === value)?.[1] ?? value
}

function chunkStateLabel(value: string) {
  return value === 'done' ? '完了' : value === 'current' ? '処理中' : '未処理'
}

function formatTime(seconds: number) {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const remainder = total % 60
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
}

function chunkRange(start: number, end: number) {
  return `${formatTime(start)}–${formatTime(end)}`
}

function kindCount(value: string) {
  return Object.values(page.value?.counts.by_kind[value] ?? {}).reduce((sum, count) => sum + count, 0)
}

async function load(silent = false) {
  const generation = ++requestGeneration
  if (!silent) loading.value = true
  error.value = ''
  try {
    const result = await api.processingJobs({
      kind: kind.value || undefined,
      status: status.value ? [status.value] : undefined,
      q: appliedSearch.value || undefined,
      limit: pageSize,
      offset: offset.value,
    })
    if (generation !== requestGeneration) return
    page.value = result
    lastUpdated.value = new Intl.DateTimeFormat('ja-JP', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    }).format(new Date())
  } catch (cause) {
    if (generation === requestGeneration)
      error.value = cause instanceof Error ? cause.message : '処理状況を読み込めませんでした'
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

async function toggleDetails(job: ProcessingJob) {
  const next = new Set(expanded.value)
  if (next.has(job.id)) {
    next.delete(job.id)
    expanded.value = next
    return
  }
  next.add(job.id)
  expanded.value = next
  if (details.value[job.id]) return
  try {
    details.value[job.id] = await api.processingJob(job.id)
  } catch (cause) {
    detailErrors.value[job.id] = cause instanceof Error ? cause.message : '区間情報を読み込めませんでした'
  }
}

async function openVideo(job: ProcessingJob) {
  try {
    const video = await api.video(job.video.id)
    ctx.openVideo(video)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '動画を開けませんでした'
  }
}

function changePage(delta: number) {
  offset.value = Math.max(0, offset.value + delta * pageSize)
  void load()
}

function schedulePoll() {
  window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(() => {
    if (document.visibilityState === 'visible') void load(true)
    schedulePoll()
  }, 20000)
}

watch([kind, status], () => {
  offset.value = 0
  void load()
})
watch(
  () => ctx.processingRefresh.value,
  () => void load(),
)
watch(search, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    appliedSearch.value = search.value.trim()
    offset.value = 0
    void load()
  }, 250)
})

const visibilityChanged = () => {
  if (document.visibilityState === 'visible') {
    void load(true)
    schedulePoll()
  } else window.clearTimeout(pollTimer)
}

onMounted(() => {
  void load()
  schedulePoll()
  document.addEventListener('visibilitychange', visibilityChanged)
})
onBeforeUnmount(() => {
  window.clearTimeout(searchTimer)
  window.clearTimeout(pollTimer)
  document.removeEventListener('visibilitychange', visibilityChanged)
})
</script>

<template>
  <section class="processing-page">
    <header class="page-head">
      <div>
        <h1>処理状況</h1>
        <p>文字起こし、要約、チャット取得の進み具合と再試行予定</p>
      </div>
      <button class="button secondary" :disabled="loading" @click="load()">
        {{ loading ? '更新中…' : '更新' }}
      </button>
    </header>

    <section v-if="page" class="processing-counts card" aria-label="処理件数">
      <div class="count-total">
        <span>すべて</span><strong>{{ page.counts.total }}</strong>
      </div>
      <div v-for="[value, label] in kinds" :key="value" class="count-kind">
        <span>{{ label }}</span
        ><strong>{{ kindCount(value) }}</strong>
      </div>
    </section>

    <div class="processing-filters">
      <label>
        <span>種類</span>
        <select v-model="kind">
          <option value="">すべて</option>
          <option v-for="[value, label] in kinds" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label>
        <span>状態</span>
        <select v-model="status">
          <option v-for="[value, label] in statuses" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label class="search-field">
        <span>動画・チャンネルを検索</span>
        <input v-model="search" type="search" placeholder="タイトル、YouTube ID、チャンネル名" />
      </label>
    </div>

    <p v-if="error" class="attention error" role="alert">{{ error }}</p>
    <div v-if="page" class="processing-list card">
      <div class="list-heading">
        <span>{{ pageStart }}–{{ pageEnd }} 件 / {{ page.total }} 件</span>
        <span>自動更新 20 秒・最終更新 {{ lastUpdated || '—' }}</span>
      </div>
      <div v-if="loading && !page.items.length" class="loading-state" aria-live="polite">読み込み中…</div>
      <p v-else-if="!page.items.length" class="empty-state">
        条件に一致する処理ジョブはありません。種類や状態の絞り込みを解除して確認してください。
      </p>
      <div v-else class="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">動画・種類</th>
              <th scope="col">状態・進捗</th>
              <th scope="col">順番・試行</th>
              <th scope="col">次回予定・エラー</th>
              <th scope="col"><span class="visually-hidden">区間</span></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="job in visibleJobs" :key="job.id">
              <tr class="job-row">
                <td data-label="動画・種類">
                  <button class="video-title text-link" @click="openVideo(job)">
                    {{ job.video.title || job.video.youtube_video_id }}
                  </button>
                  <span class="job-subtitle">{{ job.video.channel_name }} · {{ kindLabel(job.kind) }}</span>
                </td>
                <td data-label="状態・進捗">
                  <span
                    class="badge"
                    :class="
                      job.status === 'failed'
                        ? 'danger'
                        : job.status === 'processing'
                          ? 'info'
                          : job.status === 'completed'
                            ? 'success'
                            : 'warning'
                    "
                  >
                    {{ statusLabel(job.status) }}
                  </span>
                  <span v-if="job.progress" class="job-subtitle progress-text">
                    {{ job.progress.done }}/{{ job.progress.total }} 区間
                  </span>
                  <span v-if="job.current_chunk" class="job-subtitle chunk-time">
                    {{ chunkRange(job.current_chunk.start, job.current_chunk.end) }}
                  </span>
                </td>
                <td data-label="順番・試行">
                  <span>{{ job.queue_position ? `待ち順 ${job.queue_position}` : '—' }}</span>
                  <span class="job-subtitle">試行 {{ job.attempts }} 回</span>
                </td>
                <td data-label="次回予定・エラー">
                  <span>{{ job.next_attempt_at ? formatDate(job.next_attempt_at) : '—' }}</span>
                  <span v-if="job.error" class="job-error">{{ job.error }}</span>
                </td>
                <td class="expand-cell" data-label="区間">
                  <button
                    class="button secondary small"
                    :aria-label="expanded.has(job.id) ? '区間を隠す' : '区間を表示'"
                    :aria-expanded="expanded.has(job.id)"
                    @click="toggleDetails(job)"
                  >
                    {{ expanded.has(job.id) ? '閉じる' : '区間' }}
                  </button>
                </td>
              </tr>
              <tr v-if="expanded.has(job.id)" class="detail-row">
                <td colspan="5">
                  <p v-if="detailErrors[job.id]" class="error-text" role="alert">
                    {{ detailErrors[job.id] }}
                  </p>
                  <p v-else-if="!details[job.id]" class="muted">区間情報を読み込み中…</p>
                  <ol v-else-if="details[job.id].chunks.length" class="chunk-list">
                    <li
                      v-for="chunk in details[job.id].chunks"
                      :key="chunk.index"
                      :class="`chunk-${chunk.state}`"
                    >
                      <span class="chunk-index">{{ chunk.index + 1 }}</span>
                      <span class="chunk-range">{{ chunkRange(chunk.start, chunk.end) }}</span>
                      <span class="chunk-state">{{ chunkStateLabel(chunk.state) }}</span>
                      <p v-if="chunk.summary" class="chunk-summary">{{ chunk.summary }}</p>
                    </li>
                  </ol>
                  <p v-else class="muted">このジョブに処理区間はありません。</p>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <nav v-if="page.total > pageSize" class="pagination" aria-label="処理ジョブ一覧のページ">
        <button class="button secondary small" :disabled="offset === 0" @click="changePage(-1)">前へ</button>
        <span>{{ pageNumber }} / {{ pageCount }}</span>
        <button
          class="button secondary small"
          :disabled="offset + pageSize >= page.total"
          @click="changePage(1)"
        >
          次へ
        </button>
      </nav>
    </div>
    <div v-else-if="loading" class="card loading-state" aria-live="polite">処理状況を読み込み中…</div>
  </section>
</template>

<style scoped>
.processing-page {
  display: grid;
  gap: 16px;
}

.processing-counts {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
  padding: 14px 18px;
}

.count-total,
.count-kind {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.count-total strong {
  font-size: 1.15rem;
}

.count-kind span,
.count-total span {
  color: var(--muted);
  font-size: 0.875rem;
}

.count-kind strong {
  font-variant-numeric: tabular-nums;
}

.processing-filters {
  display: grid;
  grid-template-columns: minmax(150px, 0.7fr) minmax(150px, 0.7fr) minmax(240px, 1.6fr);
  gap: 12px;
  align-items: end;
}

.processing-filters label {
  display: grid;
  gap: 5px;
  color: var(--muted);
  font-size: 0.84rem;
}

.processing-filters select,
.processing-filters input {
  width: 100%;
  min-height: 40px;
}

.processing-list {
  padding: 0;
  overflow: hidden;
}

.list-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  color: var(--muted);
  font-size: 0.82rem;
}

.table-scroll {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

th,
td {
  padding: 12px 14px;
  border-top: 1px solid var(--line);
  vertical-align: top;
}

th {
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 600;
}

td {
  font-size: 0.9rem;
}

.video-title {
  display: block;
  max-width: 360px;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-subtitle {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 0.8rem;
}

.progress-text,
.chunk-time {
  font-variant-numeric: tabular-nums;
}

.job-error {
  display: block;
  max-width: 280px;
  margin-top: 4px;
  color: var(--danger);
  font-size: 0.8rem;
  overflow-wrap: anywhere;
}

.expand-cell {
  text-align: right;
  white-space: nowrap;
}

.detail-row td {
  padding: 10px 16px 16px;
  background: var(--surface-2);
}

.chunk-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.chunk-list li {
  display: grid;
  grid-template-columns: 2rem minmax(140px, 1fr) auto;
  align-items: baseline;
  gap: 8px 12px;
  padding: 9px 0;
  border-bottom: 1px solid var(--line);
}

.chunk-index,
.chunk-range {
  font-variant-numeric: tabular-nums;
}

.chunk-index,
.chunk-state {
  color: var(--muted);
  font-size: 0.82rem;
}

.chunk-summary {
  grid-column: 2 / -1;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.chunk-current .chunk-state {
  color: var(--info);
}

.chunk-done .chunk-state {
  color: var(--success);
}

.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 14px;
  padding: 14px;
}

.loading-state {
  padding: 28px 16px;
  color: var(--muted);
  text-align: center;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

@media (max-width: 760px) {
  .processing-filters {
    grid-template-columns: 1fr 1fr;
  }

  .search-field {
    grid-column: 1 / -1;
  }

  .processing-list {
    overflow: visible;
    padding: 0 12px 10px;
  }

  .list-heading {
    padding: 12px 4px;
    flex-direction: column;
    gap: 3px;
  }

  .table-scroll {
    overflow: visible;
  }

  table,
  tbody {
    display: block;
  }

  thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
  }

  .job-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 4px 12px;
    margin-top: 10px;
    padding: 12px;
    border: 1px solid var(--line);
    border-radius: 12px;
  }

  .job-row td {
    display: block;
    padding: 0;
    border: 0;
  }

  .job-row td:first-child {
    grid-column: 1 / -1;
  }

  .job-row td:nth-child(2),
  .job-row td:nth-child(3),
  .job-row td:nth-child(4) {
    grid-column: 1 / -1;
  }

  .job-row .expand-cell {
    grid-column: 2;
    grid-row: 1;
  }

  .job-row .video-title {
    max-width: calc(100% - 72px);
  }

  .job-row td:nth-child(2),
  .job-row td:nth-child(3),
  .job-row td:nth-child(4) {
    display: grid;
    grid-template-columns: minmax(96px, 0.45fr) minmax(0, 1fr);
    align-items: baseline;
    gap: 8px;
    padding-top: 5px;
  }

  .job-row td:nth-child(2)::before {
    content: '状態・進捗';
  }

  .job-row td:nth-child(3)::before {
    content: '順番・試行';
  }

  .job-row td:nth-child(4)::before {
    content: '次回予定・エラー';
  }

  .job-row td:nth-child(2)::before,
  .job-row td:nth-child(3)::before,
  .job-row td:nth-child(4)::before {
    color: var(--muted);
    font-size: 0.78rem;
  }

  .job-row td:nth-child(2) > *,
  .job-row td:nth-child(3) > *,
  .job-row td:nth-child(4) > * {
    grid-column: 2;
  }

  .detail-row {
    display: block;
    margin: 0 4px;
  }

  .detail-row td {
    display: block;
    padding: 10px 8px;
  }

  .detail-row [colspan] {
    border: 0;
  }
}

@media (max-width: 430px) {
  .processing-counts {
    gap: 10px 16px;
    padding: 12px;
  }

  .count-total,
  .count-kind {
    gap: 5px;
  }

  .processing-filters {
    grid-template-columns: 1fr;
  }

  .search-field {
    grid-column: auto;
  }

  .chunk-list li {
    grid-template-columns: 1.5rem minmax(0, 1fr) auto;
    gap: 6px;
  }
}
</style>
