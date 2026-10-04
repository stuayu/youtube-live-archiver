<script setup lang="ts">
import type { Video } from '../../api/types'
import Icon from '../ui/Icon.vue'
import { missingOpeningLabel } from '../../lib/recording'

defineProps<{ video: Video; live: boolean }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <header class="dialog-header">
    <div>
      <h2 class="player-title">{{ video.title }}</h2>
      <small>{{ video.channel_name }} <span v-if="live" class="badge danger">録画中</span></small>
      <small v-if="missingOpeningLabel(video.recording_start_offset_ms || 0, video.live_from_start)">
        {{ missingOpeningLabel(video.recording_start_offset_ms || 0, video.live_from_start) }}
        <span v-if="video.archive_backfill === 'queued'"> · アーカイブ補完中</span>
      </small>
      <small v-else-if="video.archive_backfill === 'queued'">アーカイブ補完中</small>
    </div>
    <button class="icon-button" aria-label="プレイヤーを閉じる" @click="emit('close')">
      <Icon name="close" />
    </button>
  </header>
</template>
