<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api/resources'
import { HttpError } from '../api/client'
import type { Channel, QualityPreset, RecordingRule, Storage } from '../api/types'
import Dialog from './ui/Dialog.vue'

const props = defineProps<{ open: boolean; rule?: RecordingRule | null }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const channels = ref<Channel[]>([])
const storages = ref<Storage[]>([])
const presets = ref<QualityPreset[]>([])
const form = ref<Partial<RecordingRule>>({})
const qualityMode = ref('original')
const preview = ref<Record<string, unknown> | null>(null)
const error = ref('')
const fields = ref<Record<string, string>>({})
const busy = ref(false)
const title = computed(() => (props.rule ? 'ルールを編集' : 'ルールを追加'))

watch(
  () => [props.open, props.rule] as const,
  async ([open, rule]) => {
    if (!open) return
    form.value = rule
      ? { ...rule }
      : {
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
        }
    qualityMode.value = presets.value.find((item) => item.id === form.value.quality)?.id ?? 'custom'
    preview.value = null
    error.value = ''
    fields.value = {}
    const [channelRows, storageRows, presetRows] = await Promise.all([
      api.channels(),
      api.storages(),
      api.qualityPresets(),
    ])
    channels.value = channelRows
    storages.value = storageRows
    presets.value = presetRows
    qualityMode.value = form.value.quality
      ? (presetRows.find((item) => item.id === form.value.quality)?.id ?? 'custom')
      : 'original'
  },
  { immediate: true },
)

function selectQuality(value: string) {
  qualityMode.value = value
  form.value.quality = value === 'custom' ? form.value.quality : value === 'original' ? null : value
}
function qualityChanged(event: Event) {
  if (event.target instanceof HTMLSelectElement) selectQuality(event.target.value)
}
async function runPreview() {
  busy.value = true
  error.value = ''
  try {
    preview.value = await api.previewRule(form.value)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'プレビューを取得できません'
  } finally {
    busy.value = false
  }
}
async function save() {
  busy.value = true
  error.value = ''
  fields.value = {}
  try {
    await api.saveRule(form.value, props.rule?.id)
    emit('saved')
    emit('close')
  } catch (cause) {
    if (cause instanceof HttpError) {
      error.value = cause.message
      fields.value = cause.fields
    } else error.value = cause instanceof Error ? cause.message : '保存できません'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog v-if="open" :title="title" @close="emit('close')">
    <form class="settings-form" @submit.prevent="save">
      <p v-if="error" class="inline-error">{{ error }}</p>
      <div class="form-grid">
        <label class="field">
          名前
          <input v-model="form.name" required maxlength="200" />
          <small v-if="fields.name" class="inline-error">{{ fields.name }}</small>
        </label>
        <label class="field">
          種別
          <select v-model="form.is_exclusion">
            <option :value="false">録画ルール</option>
            <option :value="true">除外ルール</option>
          </select>
        </label>
        <label class="field">
          対象
          <select v-model="form.target">
            <option value="both">タイトルと説明</option>
            <option value="title">タイトル</option>
            <option value="description">説明</option>
          </select>
        </label>
        <label class="field">
          照合方式
          <select v-model="form.match_mode">
            <option value="keyword">キーワード</option>
            <option value="regex">正規表現</option>
          </select>
        </label>
        <label class="field">
          パターン
          <input v-model="form.pattern" required maxlength="500" />
          <small v-if="fields.pattern" class="inline-error">{{ fields.pattern }}</small>
        </label>
        <label class="field">
          対象チャンネル
          <select v-model="form.channel_id">
            <option :value="null">すべて</option>
            <option v-for="channel in channels" :key="channel.id" :value="channel.id">
              {{ channel.name }}
            </option>
          </select>
        </label>
        <label class="field">
          保存先の上書き
          <select v-model="form.storage_id">
            <option :value="null">チャンネル設定を使用</option>
            <option v-for="storage in storages" :key="storage.id" :value="storage.id">
              {{ storage.name }}
            </option>
          </select>
        </label>
        <label class="field">
          画質プリセット
          <select :value="qualityMode" @change="qualityChanged">
            <option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.label }}</option>
            <option value="custom">カスタム</option>
          </select>
          <small>{{
            presets.find((item) => item.id === qualityMode)?.description || '独自の形式指定'
          }}</small>
        </label>
        <label v-if="qualityMode === 'custom'" class="field">
          カスタム画質
          <input v-model="form.quality" />
        </label>
        <label class="field">
          保存期間（日）
          <input v-model.number="form.retention_days" type="number" min="1" placeholder="チャンネル設定" />
        </label>
        <label class="field">
          優先順位
          <input v-model.number="form.priority" type="number" />
        </label>
        <label class="check-field"><input v-model="form.enabled" type="checkbox" />有効</label>
        <label class="check-field"
          ><input v-model="form.case_sensitive" type="checkbox" />大文字小文字を区別</label
        >
      </div>
      <div class="actions settings-actions">
        <button type="button" class="button secondary" :disabled="busy" @click="runPreview">
          既存動画でプレビュー
        </button>
        <button type="button" class="button secondary" @click="emit('close')">キャンセル</button>
        <button class="button primary" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</button>
      </div>
      <pre v-if="preview" class="summary-text">{{ JSON.stringify(preview, null, 2) }}</pre>
    </form>
  </Dialog>
</template>
