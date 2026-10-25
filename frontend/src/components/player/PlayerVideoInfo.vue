<script setup lang="ts">
import { apiUrl } from '../../api/client'
import type { Video } from '../../api/types'
import { formatBytes, formatDate, formatDuration } from '../../lib/format'
import { videoStatus } from '../../lib/status'

defineProps<{ video: Video; busy: boolean; note?: string }>()
const emit = defineEmits<{ action: [path: string, method?: string] }>()
</script>

<template>
  <section class="player-video-info surface-card" aria-label="動画情報">
    <dl class="player-meta">
      <div>
        <dt>録画日時</dt>
        <dd>{{ formatDate(video.recorded_at) }}</dd>
      </div>
      <div>
        <dt>長さ</dt>
        <dd>{{ formatDuration(video.duration_seconds) }}</dd>
      </div>
      <div>
        <dt>容量</dt>
        <dd>{{ formatBytes(video.file_size) }}</dd>
      </div>
      <div>
        <dt>保存先</dt>
        <dd>{{ video.storage_name || '未設定' }}</dd>
      </div>
      <div>
        <dt>保存期限</dt>
        <dd>{{ video.retention_expires_at ? formatDate(video.retention_expires_at) : '永久保存' }}</dd>
      </div>
      <div>
        <dt>状態</dt>
        <dd>
          <span class="badge" :class="videoStatus(video.status).tone">
            {{ videoStatus(video.status).label }}
          </span>
        </dd>
      </div>
    </dl>
    <div class="actions">
      <a class="button secondary" :href="apiUrl(`/videos/${video.id}/file?download=true`)"> ダウンロード </a>
      <button
        class="button secondary"
        :disabled="busy || !video.has_file"
        @click="emit('action', 'convert/remux')"
      >
        MP4 リマックス
      </button>
      <button
        class="button secondary"
        :disabled="busy || !video.has_file"
        @click="emit('action', 'convert/h264')"
      >
        H.264 変換
      </button>
      <button
        class="button secondary"
        :disabled="busy"
        @click="emit('action', 'protect', video.protected ? 'DELETE' : 'POST')"
      >
        {{ video.protected ? '保護解除' : '保護' }}
      </button>
    </div>
    <p v-if="note" class="meta" role="status">{{ note }}</p>
  </section>
</template>
