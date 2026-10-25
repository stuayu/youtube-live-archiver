<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>
<template>
  <section>
    <header class="page-head">
      <div>
        <h1>アクティビティ</h1>
        <p>監視・録画・保護の記録</p>
      </div>
    </header>
    <div class="toolbar">
      <label class="field"
        >チャンネル<select
          v-model="ctx.values.channel"
          @change="
            ctx.api.events({ limit: 50, channel_id: ctx.values.channel }).then((rows) => (ctx.events = rows))
          "
        >
          <option :value="undefined">すべて</option>
          <option v-for="channel in ctx.channels" :key="channel.id" :value="channel.id">
            {{ channel.name }}
          </option>
        </select>
      </label>
    </div>
    <div v-for="event in ctx.events" :key="event.id" class="event-row">
      <time :title="ctx.formatDate(event.created_at)">
        {{ ctx.relativeTime(event.created_at) }}
        <small>{{ ctx.formatDate(event.created_at) }}</small>
      </time>
      <div>
        <b>{{ ctx.labelForEvent(event) }}</b>
        <small
          >{{ event.channel_name || 'システム'
          }}<span v-if="event.video_title"> · {{ event.video_title }}</span>
        </small>
      </div>
    </div>
    <p v-if="!ctx.events.length" class="empty-state">イベントはありません。</p>
    <button
      v-if="ctx.events.length"
      class="button secondary"
      @click="
        ctx.api
          .events({ limit: 50, before_id: Number(ctx.events[ctx.events.length - 1]?.id) })
          .then((rows) => ctx.events.push(...rows))
      "
    >
      さらに読み込む
    </button>
  </section>
</template>
