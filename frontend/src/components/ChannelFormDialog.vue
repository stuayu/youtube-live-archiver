<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api/resources'
import { HttpError } from '../api/client'
import { bytesToGb, gbToBytes } from '../lib/format'
import type { Channel, ChannelInput, ChannelPreview, QualityPreset, Storage } from '../api/types'
import ChannelIdentityField from './ChannelIdentityField.vue'
import Icon from './ui/Icon.vue'
import type { ActivityProfile } from '../api/types'
import { activitySummary, localActivityHours } from '../lib/activityProfile'
const props = defineProps<{
  channel?: Channel | null
}>()
const emit = defineEmits<{
  close: []
  saved: [channel: Channel]
}>()
const initial = () => ({
  url: '',
  name: '',
  enabled: true,
  poll_interval: 'AUTO',
  storage_id: null as string | null,
  fallback_storage_id: null as string | null,
  retention_days: 30 as number | null,
  deletion_grace_days: 3,
  backfill_limit: 0,
  live_quality: '',
  archive_quality: 'bv*[vcodec^=av01]+ba/bv*[vcodec^=vp9]+ba/bv*+ba/b',
  cookie_profile: '',
  auto_protect_private: true,
  auto_protect_deleted: true,
  auto_protect_edited: true,
  edit_detection_mode: 'standard',
  always_protect: false,
  live_playback_enabled: false,
  live_from_start: true,
  live_playback_limit_bytes: 5 * 1024 ** 3,
  chat_enabled: true,
})
const formRoot = ref<HTMLFormElement | null>(null)
const form = reactive(initial())
const snapshot = ref<ReturnType<typeof initial> | null>(null)
const storageList = ref<Storage[]>([])
const qualityPresets = ref<QualityPreset[]>([])
const preview = ref<ChannelPreview | null>(null)
const previewMessage = ref('')
const errors = ref<Record<string, string>>({})
const busy = ref(false)
const applyRetention = ref(false)
const retentionCustom = ref(false)
const liveGb = ref(5)
const archiveQualityCustom = ref(false)
const activity = ref<ActivityProfile | null>(null)
const activityHours = computed(() => (activity.value ? localActivityHours(activity.value) : []))
const liveQualityCustom = ref(false)
const archivePreset = computed({
  get: () =>
    archiveQualityCustom.value
      ? 'custom'
      : (qualityPresets.value.find((preset) => preset.selector === form.archive_quality)?.id ?? 'custom'),
  set: (id: string) => {
    archiveQualityCustom.value = id === 'custom'
    if (id === 'custom') return
    const preset = qualityPresets.value.find((item) => item.id === id)
    if (preset) form.archive_quality = preset.selector
  },
})
const livePreset = computed({
  get: () =>
    liveQualityCustom.value
      ? 'custom'
      : (qualityPresets.value.find((preset) => preset.selector === form.live_quality)?.id ?? 'original'),
  set: (id: string) => {
    liveQualityCustom.value = id === 'custom'
    if (id === 'custom') return
    const preset = qualityPresets.value.find((item) => item.id === id)
    if (preset) form.live_quality = preset.selector
  },
})
const title = computed(() => (props.channel ? 'チャンネル設定' : 'チャンネルを追加'))
function dialogKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key === 'Tab' && formRoot.value) {
    const nodes = [
      ...formRoot.value.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]',
      ),
    ]
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
onMounted(() => {
  document.body.classList.add('dialog-open')
  formRoot.value?.querySelector<HTMLElement>('input')?.focus()
  document.addEventListener('keydown', dialogKey)
})
onBeforeUnmount(() => {
  document.body.classList.remove('dialog-open')
  document.removeEventListener('keydown', dialogKey)
})
watch(
  () => props.channel,
  async (channel) => {
    Object.assign(form, initial())
    errors.value = {}
    preview.value = null
    activity.value = null
    previewMessage.value = ''
    if (channel) {
      for (const key of Object.keys(form) as Array<keyof typeof form>) {
        const value = channel[key]
        if (value !== undefined) Object.assign(form, { [key]: value })
      }
      form.url = channel.url
      form.storage_id = channel.storage_id
      form.fallback_storage_id = channel.fallback_storage_id
      retentionCustom.value = ![null, 7, 14, 30, 60, 90, 180, 365].includes(channel.retention_days)
      liveGb.value = Number((channel.live_playback_limit_bytes / 1024 ** 3).toFixed(2))
      snapshot.value = { ...form }
    } else {
      snapshot.value = null
    }
  },
  { immediate: true },
)
watch(
  () => [props.channel?.id, form.poll_interval] as const,
  ([channelId, interval]) => {
    activity.value = null
    if (channelId && interval === 'AUTO') {
      void api
        .activityProfile(channelId)
        .then((value) => (activity.value = value))
        .catch(() => {})
    }
  },
  { immediate: true },
)
void api
  .storages()
  .then((items) => (storageList.value = items))
  .catch(() => {})
void api
  .qualityPresets()
  .then((items) => (qualityPresets.value = items))
  .catch(() => {})
async function runPreview() {
  previewMessage.value = ''
  try {
    preview.value = await api.previewChannel(form.url.trim())
    form.name = preview.value.name
  } catch (error) {
    preview.value = null
    previewMessage.value = error instanceof Error ? error.message : 'プレビューに失敗しました'
  }
}
const retentionChoice = computed({
  get: () =>
    retentionCustom.value
      ? 'custom'
      : form.retention_days === null
        ? 'permanent'
        : String(form.retention_days),
  set: (value: string) => {
    if (value === 'custom') {
      retentionCustom.value = true
      form.retention_days = 30
    } else {
      retentionCustom.value = false
      form.retention_days = value === 'permanent' ? null : Number(value)
    }
  },
})
function patchBody(): ChannelInput {
  const body: ChannelInput = {}
  const before = snapshot.value
  for (const key of Object.keys(form) as Array<keyof typeof form>) {
    const value = form[key]
    if (!before || JSON.stringify(value) !== JSON.stringify(before[key]))
      Object.assign(body, { [key]: value })
  }
  if (liveGb.value !== Number((props.channel?.live_playback_limit_bytes ?? 5 * 1024 ** 3) / 1024 ** 3))
    body.live_playback_limit_bytes = gbToBytes(liveGb.value) ?? 0
  return body
}
async function save() {
  busy.value = true
  errors.value = {}
  try {
    const data: ChannelInput = props.channel
      ? patchBody()
      : { ...form, url: form.url.trim(), live_playback_limit_bytes: gbToBytes(liveGb.value) ?? 0 }
    const result = props.channel
      ? await api.patchChannel(
          props.channel.id,
          data,
          applyRetention.value && snapshot.value?.retention_days !== form.retention_days,
        )
      : await api.addChannel(data)
    emit('saved', result)
  } catch (error) {
    if (error instanceof HttpError) {
      errors.value = error.fields
      if (!Object.keys(error.fields).length) errors.value = { _form: error.message }
    } else errors.value = { _form: error instanceof Error ? error.message : '保存に失敗しました' }
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="dialog-backdrop" @mousedown.self="emit('close')">
    <form
      ref="formRoot"
      class="dialog dialog-wide channel-form-dialog"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
      @submit.prevent="save"
    >
      <header class="dialog-header">
        <h2>{{ title }}</h2>
        <button type="button" class="icon-button" aria-label="閉じる" @click="emit('close')">
          <Icon name="close" />
        </button>
      </header>
      <div class="dialog-body">
        <p v-if="errors._form" class="inline-error">{{ errors._form }}</p>
        <ChannelIdentityField
          :channel="channel"
          :preview="preview"
          :preview-message="previewMessage"
          :url="form.url"
          :url-error="errors.url"
          @update:url="form.url = $event"
          @preview="runPreview"
        />
        <fieldset>
          <legend>監視</legend>
          <div class="form-grid">
            <label class="check-field">
              <input v-model="form.enabled" type="checkbox" /> 監視を有効にする</label
            >
            <div class="field">
              <label for="poll-interval">監視間隔</label>
              <select id="poll-interval" v-model="form.poll_interval">
                <option value="AUTO">自動（配信の多い時間帯に合わせる）</option>
                <option value="30">30 分</option>
                <option value="45">45 分</option>
                <option value="60">60 分</option>
                <option value="90">90 分</option>
                <option value="120">2 時間</option>
                <option value="180">3 時間</option>
                <option value="240">4 時間</option>
                <option value="360">6 時間</option>
              </select>
              <small v-if="errors.poll_interval" class="inline-error">{{ errors.poll_interval }}</small>
              <small>自動は配信状況に合わせて確認間隔を調整します。</small>
              <div v-if="form.poll_interval === 'AUTO' && channel && activity" class="activity-profile">
                <div class="activity-hours" aria-label="ローカル時刻の配信時間帯">
                  <span v-for="(level, hour) in activityHours" :key="hour" :class="`level-${level}`">
                    {{ hour
                    }}<small>{{
                      level === 'hot' ? '多' : level === 'warm' ? '中' : level === 'cold' ? '少' : '未'
                    }}</small>
                  </span>
                </div>
                <small>{{ activitySummary(activity) }}</small>
              </div>
              <small v-else-if="form.poll_interval !== 'AUTO'"
                >常に約
                {{ form.poll_interval }} 分ごとに確認します。配信予定の開始前後は自動で間隔を詰めます</small
              >
            </div>
          </div>
        </fieldset>
        <fieldset>
          <legend>保存</legend>
          <div class="form-grid">
            <div class="field">
              <label for="storage-id">保存先</label>
              <select id="storage-id" v-model="form.storage_id">
                <option :value="null">既定の保存先</option>
                <option v-for="storage in storageList" :key="storage.id" :value="storage.id">
                  {{ storage.name }}
                </option>
              </select>
              <small>指定しない場合はアプリの既定保存先を使います。</small>
            </div>
            <div class="field">
              <label for="fallback-id">予備保存先</label>
              <select id="fallback-id" v-model="form.fallback_storage_id">
                <option :value="null">なし</option>
                <option v-for="storage in storageList" :key="storage.id" :value="storage.id">
                  {{ storage.name }}
                </option>
              </select>
            </div>
            <div class="field">
              <label for="retention">保存期間</label>
              <select id="retention" v-model="retentionChoice">
                <option value="permanent">永久</option>
                <option v-for="days in [7, 14, 30, 60, 90, 180, 365]" :key="days" :value="String(days)">
                  {{ days }} 日
                </option>
                <option value="custom">カスタム</option>
              </select>
            </div>
            <div v-if="retentionCustom" class="field">
              <label for="retention-custom">カスタム保存期間（日）</label>
              <input id="retention-custom" v-model.number="form.retention_days" type="number" min="1" />
              <small v-if="errors.retention_days" class="inline-error">{{ errors.retention_days }}</small>
            </div>
            <div class="field">
              <label for="grace">削除猶予（日）</label>
              <input id="grace" v-model.number="form.deletion_grace_days" type="number" min="0" />
              <small>保存期限が来たあと削除前に待つ日数です。</small>
            </div>
            <div class="field">
              <label for="backfill">過去配信の取込数</label>
              <input id="backfill" v-model.number="form.backfill_limit" type="number" min="0" />
              <small>登録時に過去から遡って確認する配信の上限です。</small>
            </div>
          </div>
          <label v-if="channel" class="check-field">
            <input v-model="applyRetention" type="checkbox" /> 保存期間の変更を既存録画にも適用</label
          >
        </fieldset>
        <fieldset>
          <legend>画質</legend>
          <div class="form-grid">
            <div class="field">
              <label for="live-quality">ライブ画質</label>
              <select id="live-quality" v-model="livePreset">
                <option v-for="preset in qualityPresets" :key="preset.id" :value="preset.id">
                  {{ preset.label }}
                </option>
                <option value="custom">カスタム</option>
              </select>
              <small>{{
                qualityPresets.find((item) => item.id === livePreset)?.description || '独自の形式指定'
              }}</small>
              <input
                v-if="livePreset === 'custom'"
                v-model="form.live_quality"
                aria-label="ライブ画質のカスタム指定"
              />
            </div>
            <div class="field">
              <label for="archive-quality">アーカイブ画質</label>
              <select id="archive-quality" v-model="archivePreset">
                <option v-for="preset in qualityPresets" :key="preset.id" :value="preset.id">
                  {{ preset.label }}
                </option>
                <option value="custom">カスタム</option>
              </select>
              <small>{{
                qualityPresets.find((item) => item.id === archivePreset)?.description || '独自の形式指定'
              }}</small>
              <input
                v-if="archivePreset === 'custom'"
                v-model="form.archive_quality"
                aria-label="アーカイブ画質のカスタム指定"
              />
            </div>
          </div>
        </fieldset>
        <fieldset>
          <legend>保護</legend>
          <div class="form-grid">
            <div class="field">
              <label for="edit-mode">編集検出</label>
              <select id="edit-mode" v-model="form.edit_detection_mode">
                <option value="off">無効</option>
                <option value="basic">基本</option>
                <option value="standard">標準</option>
                <option value="strong">強</option>
              </select>
              <small>強いほど変更を検出しやすくなりますが、誤検知の可能性も上がります。</small>
            </div>
            <label class="check-field">
              <input v-model="form.auto_protect_private" type="checkbox" /> 非公開時に保護</label
            >
            <label class="check-field">
              <input v-model="form.auto_protect_deleted" type="checkbox" /> 削除時に保護</label
            >
            <label class="check-field">
              <input v-model="form.auto_protect_edited" type="checkbox" /> 編集時に保護</label
            >
            <label class="check-field">
              <input v-model="form.always_protect" type="checkbox" /> 常に永久保存</label
            >
          </div>
        </fieldset>
        <fieldset>
          <legend>追加機能</legend>
          <div class="form-grid">
            <label class="check-field">
              <input v-model="form.chat_enabled" type="checkbox" /> チャットを保存
            </label>
            <div class="field">
              <label for="cookie-profile">Cookie Profile</label>
              <input id="cookie-profile" v-model="form.cookie_profile" />
              <small>ログインが必要なチャンネルで登録済みプロファイル名を指定します。</small>
            </div>
            <label class="check-field">
              <input v-model="form.live_playback_enabled" type="checkbox" /> 追っかけ視聴を有効にする</label
            >
            <div class="field">
              <label class="check-field">
                <input
                  v-model="form.live_from_start"
                  type="checkbox"
                  :disabled="form.live_playback_enabled"
                />
                配信の先頭から録画する
              </label>
              <small
                >配信の途中で録画が始まっても、先頭までさかのぼって保存します。追っかけ視聴を有効にしたチャンネルでは使えません。</small
              >
            </div>
            <div class="field">
              <label for="live-limit">HLS 上限（GB）</label>
              <input id="live-limit" v-model.number="liveGb" type="number" min="0" step="0.1" />
              <small>現在: {{ bytesToGb(form.live_playback_limit_bytes) }} GB</small>
            </div>
          </div>
        </fieldset>
      </div>
      <footer class="dialog-footer">
        <button type="button" class="button secondary" @click="emit('close')">キャンセル</button>
        <button class="button primary" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</button>
      </footer>
    </form>
  </div>
</template>
