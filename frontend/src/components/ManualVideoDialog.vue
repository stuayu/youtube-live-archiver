<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api/resources'
import { HttpError } from '../api/client'
import { videoStatus } from '../lib/status'
import type { ManualVideoPreview, Storage } from '../api/types'

const props = defineProps<{ storages: Storage[] }>()
const emit = defineEmits<{ close: []; added: [target: 'upcoming' | 'videos'] }>()
const root = ref<HTMLElement | null>(null)
const url = ref('')
const preview = ref<ManualVideoPreview | null>(null)
const error = ref('')
const fieldError = ref('')
const storageId = ref<string | null>(null)
const protectedVideo = ref(false)
const busy = ref(false)
const fallbackThumbnail = (event: Event) => {
  const image = event.target as HTMLImageElement
  if (!image.dataset.fallback) {
    image.dataset.fallback = 'true'
    image.src = '/icon.svg'
  }
}
const allowed = computed(
  () =>
    !preview.value?.existing ||
    ['WAITING_LIVE', 'SKIPPED', 'CANCELLED'].includes(preview.value.existing.status),
)
function clearPreview() {
  preview.value = null
  fieldError.value = ''
}

function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
  }
  if (event.key === 'Tab' && root.value) {
    const nodes = [
      ...root.value.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input:not(:disabled),select:not(:disabled)',
      ),
    ]
    if (event.shiftKey && document.activeElement === nodes[0]) {
      event.preventDefault()
      nodes[nodes.length - 1]?.focus()
    } else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) {
      event.preventDefault()
      nodes[0]?.focus()
    }
  }
}
onMounted(() => {
  document.body.classList.add('dialog-open')
  root.value?.querySelector<HTMLInputElement>('input')?.focus()
  document.addEventListener('keydown', keydown)
})
onBeforeUnmount(() => {
  document.body.classList.remove('dialog-open')
  document.removeEventListener('keydown', keydown)
})
async function confirmUrl() {
  error.value = ''
  fieldError.value = ''
  preview.value = null
  if (!url.value.trim()) {
    fieldError.value = 'YouTube 動画 URL または 11 文字の動画 ID を入力してください'
    return
  }
  busy.value = true
  try {
    preview.value = await api.previewManualVideo(url.value)
  } catch (cause) {
    if (cause instanceof HttpError) fieldError.value = cause.fields.url || cause.message
    else fieldError.value = cause instanceof Error ? cause.message : '動画を確認できません'
  } finally {
    busy.value = false
  }
}
async function add() {
  if (!preview.value || !allowed.value) return
  busy.value = true
  fieldError.value = ''
  try {
    const result = await api.addManualVideo({
      url: url.value,
      storage_id: storageId.value,
      protected: protectedVideo.value,
    })
    emit('added', result.action === 'wait_live' ? 'upcoming' : 'videos')
  } catch (cause) {
    if (cause instanceof HttpError && cause.status === 409) fieldError.value = cause.message
    else if (cause instanceof HttpError) fieldError.value = cause.fields.url || cause.message
    else fieldError.value = cause instanceof Error ? cause.message : '動画を追加できません'
  } finally {
    busy.value = false
  }
}
function stateAction(item: ManualVideoPreview) {
  if (item.action === 'record_live') return '配信中 → すぐ録画を開始します'
  if (item.action === 'wait_live') {
    const start = item.scheduled_start_at
      ? new Date(item.scheduled_start_at).toLocaleString('ja-JP', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        })
      : '時刻未定'
    return `配信予定（${start}）→ 開始時に録画します`
  }
  return '配信終了 → アーカイブをダウンロードします'
}
</script>

<template>
  <div class="dialog-backdrop" @click.self="emit('close')">
    <section
      ref="root"
      class="dialog manual-video-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="manual-video-title"
    >
      <header class="dialog-header">
        <h2 id="manual-video-title">URL から動画を追加</h2>
        <button class="icon-button" aria-label="閉じる" @click="emit('close')">×</button>
      </header>
      <div class="dialog-body manual-video-body">
        <form @submit.prevent="confirmUrl">
          <label class="field"
            >YouTube 動画 URL または動画 ID
            <input
              v-model="url"
              autocomplete="url"
              placeholder="https://www.youtube.com/watch?v=…"
              @input="clearPreview"
            />
          </label>
          <small v-if="fieldError" class="inline-error" role="alert">{{ fieldError }}</small>
          <div class="dialog-actions">
            <button class="button secondary" type="submit" :disabled="busy">
              {{ busy ? '確認中…' : '確認' }}
            </button>
          </div>
        </form>
        <div v-if="preview" class="manual-preview">
          <img v-if="preview.thumbnail" :src="preview.thumbnail" alt="" @error="fallbackThumbnail" />
          <div>
            <strong>{{ preview.title }}</strong>
            <p>{{ preview.channel.name }}</p>
            <p v-if="!preview.channel.registered">
              未登録のチャンネルです。監視しないチャンネルとして登録します
            </p>
            <p>{{ stateAction(preview) }}</p>
            <p v-if="preview.existing">
              登録済み: {{ videoStatus(preview.existing.status).label
              }}<span v-if="!allowed">。この状態から追加できません</span>
            </p>
          </div>
        </div>
        <template v-if="preview && allowed">
          <label class="field"
            >保存先
            <select v-model="storageId">
              <option :value="null">チャンネルの設定に従う</option>
              <option v-for="storage in props.storages" :key="storage.id" :value="storage.id">
                {{ storage.name }}
              </option>
            </select>
          </label>
          <label class="check-field"><input v-model="protectedVideo" type="checkbox" />保護する</label>
        </template>
      </div>
      <footer class="dialog-footer">
        <button class="button secondary" @click="emit('close')">閉じる</button>
        <button class="button primary" :disabled="busy || !preview || !allowed" @click="add">追加</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.manual-video-dialog {
  width: min(560px, 100%);
}
.manual-video-dialog :deep(.manual-video-body) {
  min-height: 0;
  padding: 16px 18px;
}
.manual-preview {
  display: grid;
  grid-template-columns: minmax(100px, 38%) 1fr;
  gap: 14px;
  margin: 18px 0;
}
.manual-preview img {
  width: 100%;
  border-radius: 8px;
}
.manual-preview p {
  margin: 8px 0;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
@media (max-width: 480px) {
  .manual-preview {
    grid-template-columns: 1fr;
  }
  .manual-preview img {
    max-height: 180px;
    object-fit: cover;
  }
}
</style>
