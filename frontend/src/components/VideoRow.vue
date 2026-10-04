<script setup lang="ts">
import type { Video } from '../api/types'
import { videoStatus } from '../lib/status'
import { formatBytes, formatDate } from '../lib/format'
import { missingOpeningLabel } from '../lib/recording'
import Icon from './ui/Icon.vue'
const props = defineProps<{
  video: Video
  deleted?: boolean
}>()
const fallbackThumbnail = (event: Event) => {
  const image = event.target as HTMLImageElement
  if (!image.dataset.fallback) {
    image.dataset.fallback = 'true'
    image.src = '/icon.svg'
  }
}
const emit = defineEmits<{
  play: [video: Video]
  stop: [video: Video]
  protect: [video: Video]
  remove: [video: Video]
}>()
const menuActions = (video: Video) => ({
  canDelete: video.status !== 'RECORDING' && !props.deleted,
  canProtect: video.status !== 'RECORDING',
  canPlay: video.has_file || video.live_playback_available,
})
</script>
<template>
  <article class="card video-row">
    <img
      class="thumbnail"
      loading="lazy"
      decoding="async"
      :src="video.has_thumbnail ? `/api/videos/${video.id}/thumbnail` : '/icon.svg'"
      :alt="video.title"
      @error="fallbackThumbnail"
    />
    <div class="item-main">
      <div class="item-title">{{ video.title }}</div>
      <div class="meta">
        <span>{{ video.channel_name }} · {{ formatDate(video.recorded_at) }}</span>
        <span class="badge" :class="videoStatus(video.status).tone">{{
          videoStatus(video.status).label
        }}</span>
        <span>{{ formatBytes(video.file_size) }}</span>
        <span v-if="missingOpeningLabel(video.recording_start_offset_ms || 0, video.live_from_start)">{{
          missingOpeningLabel(video.recording_start_offset_ms || 0, video.live_from_start)
        }}</span>
        <span v-if="video.archive_backfill === 'queued'">アーカイブ補完中</span>
      </div>
    </div>
    <div class="actions">
      <button v-if="video.status === 'RECORDING'" class="button danger small" @click="emit('stop', video)">
        停止
      </button>
      <button
        v-else-if="menuActions(video).canPlay"
        class="button primary small"
        @click="emit('play', video)"
      >
        再生
      </button>
      <details class="menu">
        <summary class="button small" aria-label="動画操作"><Icon name="more" /></summary>
        <div class="menu-items">
          <button v-if="menuActions(video).canProtect" @click="emit('protect', video)">
            {{ video.protected ? '保護解除' : '永久保存' }}
          </button>
          <button v-if="menuActions(video).canDelete" class="destructive" @click="emit('remove', video)">
            削除
          </button>
        </div>
      </details>
    </div>
  </article>
</template>
