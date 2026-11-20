<script setup lang="ts">
import { computed } from 'vue'
import type { EditAnalysis } from '../../api/types'
import { formatDuration } from '../../lib/format'
import { youtubeAt } from '../../lib/editMarkers'
import { protectionReason as reasonLabel } from '../../lib/status'

const props = defineProps<{
  analysis: EditAnalysis
  youtubeVideoId: string
  protectionReason: string | null
  busy: boolean
}>()
const emit = defineEmits<{ seek: [seconds: number]; run: [] }>()

const running = computed(() => ['pending', 'processing'].includes(props.analysis.status))
const result = computed(() => props.analysis.result)
const unreliable = computed(() => (result.value?.matched_ratio ?? 1) < 0.5)
const editedReason = computed(() =>
  ['REMOTE_DURATION_CHANGED', 'REMOTE_CONTENT_CHANGED'].includes(props.protectionReason || ''),
)

function span(seconds: number) {
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return [h && `${h}時間`, m && `${m}分`, s && `${s}秒`].filter(Boolean).join('') || '0秒'
}
</script>

<template>
  <section class="edit-analysis" aria-label="YouTube 版との差分">
    <p v-if="editedReason" class="edit-analysis-reason">
      保護理由: {{ reasonLabel(protectionReason) }}。下の一覧で編集された位置を確認できます。
    </p>
    <p v-if="analysis.status === 'none'" class="meta">
      YouTube の現在のアーカイブと録画の音声を照合して、カット・追加された区間を調べます。
    </p>
    <p v-else-if="running" class="meta" role="status">
      解析中です。YouTube の音声を取得して録画と照合しています（長い配信では数十分かかります）。
    </p>
    <p
      v-else-if="analysis.status === 'retry_wait' || analysis.status === 'failed'"
      class="edit-analysis-error"
      role="alert"
    >
      解析できませんでした{{ analysis.status === 'retry_wait' ? '（自動で再試行します）' : '' }}:
      {{ analysis.error }}
    </p>

    <template v-if="result">
      <p class="meta">
        録画 {{ formatDuration(result.local_duration) }} / YouTube 版
        {{ formatDuration(result.remote_duration) }} · 一致率 {{ Math.round(result.matched_ratio * 100) }}%
      </p>
      <p v-if="unreliable" class="edit-analysis-warning">
        一致した区間が少ないため、結果は参考程度です（音声が差し替えられている可能性があります）。
      </p>
      <p v-if="!result.removed.length && !result.added.length" class="empty-state">
        YouTube 版との違いは見つかりませんでした
      </p>

      <h3 v-if="result.removed.length" class="edit-analysis-heading">
        YouTube 版でカットされた区間 {{ result.removed.length }} 件
      </h3>
      <ul v-if="result.removed.length" class="edit-region-list">
        <li
          v-for="item in result.removed"
          :key="`removed-${item.start}`"
          class="edit-region edit-region-removed"
        >
          <button type="button" class="edit-region-seek" @click="emit('seek', item.start)">
            <span class="edit-region-time"
              >{{ formatDuration(item.start) }}〜{{ formatDuration(item.end) }}</span
            >
            <span class="edit-region-length">{{ span(item.end - item.start) }}</span>
          </button>
          <a
            v-if="item.remote_at !== null"
            class="edit-region-youtube"
            :href="youtubeAt(youtubeVideoId, item.remote_at)"
            target="_blank"
            rel="noopener noreferrer"
          >
            YouTube 版 {{ formatDuration(item.remote_at) }} の位置
          </a>
        </li>
      </ul>

      <h3 v-if="result.added.length" class="edit-analysis-heading">
        YouTube 版にだけある区間 {{ result.added.length }} 件
      </h3>
      <ul v-if="result.added.length" class="edit-region-list">
        <li v-for="item in result.added" :key="`added-${item.start}`" class="edit-region edit-region-added">
          <a
            class="edit-region-youtube"
            :href="youtubeAt(youtubeVideoId, item.start)"
            target="_blank"
            rel="noopener noreferrer"
          >
            <span class="edit-region-time"
              >{{ formatDuration(item.start) }}〜{{ formatDuration(item.end) }}</span
            >
            <span class="edit-region-length">{{ span(item.end - item.start) }}</span>
          </a>
          <button
            v-if="item.local_at !== null"
            type="button"
            class="edit-region-seek"
            @click="emit('seek', item.local_at)"
          >
            録画 {{ formatDuration(item.local_at) }} の位置
          </button>
        </li>
      </ul>
    </template>

    <div class="actions">
      <button
        type="button"
        class="button secondary edit-analysis-run"
        :disabled="busy || running"
        @click="emit('run')"
      >
        {{ analysis.status === 'none' ? 'YouTube 版と比較する' : '再解析する' }}
      </button>
    </div>
  </section>
</template>
