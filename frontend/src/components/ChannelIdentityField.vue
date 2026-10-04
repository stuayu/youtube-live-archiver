<script setup lang="ts">
import type { Channel, ChannelPreview } from '../api/types'

defineProps<{
  channel?: Channel | null
  preview: ChannelPreview | null
  previewMessage: string
  url: string
  urlError?: string
}>()
const emit = defineEmits<{ 'update:url': [value: string]; preview: [] }>()
</script>

<template>
  <div v-if="channel" class="channel-identity">
    <img
      :src="channel.has_avatar ? `/api/channels/${channel.id}/avatar` : '/icon.svg'"
      :alt="`${channel.name} のアイコン`"
    />
    <div>
      <b>{{ channel.name }}</b>
      <small>{{ channel.youtube_channel_id }}</small>
    </div>
  </div>
  <div v-else class="field">
    <label for="channel-url">チャンネル URL / @handle / ID</label>
    <div class="inline-control">
      <input
        id="channel-url"
        required
        :value="url"
        placeholder="https://youtube.com/@example または @example / UC…"
        @input="emit('update:url', ($event.target as HTMLInputElement).value)"
      />
      <button type="button" class="button secondary" @click="emit('preview')">プレビュー</button>
    </div>
    <small v-if="urlError" class="inline-error">{{ urlError }}</small>
  </div>
  <div v-if="preview || previewMessage" class="preview-panel">
    <img v-if="preview?.avatar" :src="preview.avatar" alt="" />
    <div>
      <b>{{ preview?.name || 'プレビューを取得できません' }}</b>
      <small>{{ preview?.channel_id || previewMessage }}</small>
    </div>
    <span v-if="previewMessage">このまま登録を試せます</span>
  </div>
</template>
