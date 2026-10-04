<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../composables/workspaceContext'
import Icon from '../components/ui/Icon.vue'
const ctx = reactive(useWorkspaceContext())
</script>
<template>
  <section>
    <header class="page-head">
      <div>
        <h1>配信予定</h1>
        <p>{{ ctx.filteredUpcoming.length }} 件 · 日付ごとに確認</p>
      </div>
    </header>
    <div class="toolbar">
      <label class="field grow"
        >検索<input v-model="ctx.upcomingSearch" placeholder="タイトル・チャンネル" />
      </label>
      <label class="field"
        >チャンネル<select v-model="ctx.values.channel">
          <option :value="undefined">すべて</option>
          <option v-for="channel in ctx.channels" :key="channel.id" :value="channel.id">
            {{ channel.name }}
          </option>
        </select>
      </label>
      <label class="field"
        >開始日（以降）<input v-model="ctx.upcomingFrom" lang="ja-JP" type="date" />
      </label>
      <label class="field">終了日（まで）<input v-model="ctx.upcomingTo" lang="ja-JP" type="date" /> </label>
    </div>
    <div v-if="ctx.loading && !ctx.upcoming.length" class="skeleton"></div>
    <div v-else-if="!ctx.filteredUpcoming.length" class="empty-state">配信予定はありません。</div>
    <section v-for="group in ctx.upcomingGroups" :key="group.label" class="upcoming-group">
      <h2>{{ group.label }}</h2>
      <div class="list">
        <article v-for="video in group.items" :key="video.id" class="card video-row">
          <img
            class="thumbnail"
            loading="lazy"
            decoding="async"
            :src="video.has_thumbnail ? ctx.apiUrl(`/videos/${video.id}/thumbnail`) : '/icon.svg'"
            :alt="video.title"
          />
          <div class="item-main">
            <div class="item-title">{{ video.title }}</div>
            <div class="meta">
              <span>{{ video.channel_name }}</span>
              <span>{{
                video.scheduled_start_at ? ctx.formatDate(video.scheduled_start_at) : '時刻未定'
              }}</span>
              <span>{{ ctx.upcomingStartLabel(video) }}</span>
              <span
                class="badge"
                :class="video.status === 'RECORDING' ? 'danger' : video.schedule_enabled ? 'info' : 'neutral'"
                >{{
                  video.status === 'RECORDING' ? '録画中' : video.schedule_enabled ? '録画予約' : '録画しない'
                }}</span
              >
            </div>
          </div>
          <div class="actions">
            <label class="check-field">
              <input
                type="checkbox"
                :checked="video.schedule_enabled"
                :disabled="video.status === 'RECORDING'"
                @change="ctx.toggleSchedule(video)"
              />録画する</label
            >
            <details class="menu">
              <summary class="button small" aria-label="配信予定の操作"><Icon name="more" /></summary>
              <div class="menu-items">
                <button
                  :disabled="video.status === 'RECORDING'"
                  @click="
                    ctx.api
                      .action(video.id, 'recording/try')
                      .then(() => {
                        ctx.push('録画を要求しました')
                        ctx.load(true)
                      })
                      .catch((e: unknown) =>
                        ctx.push(e instanceof Error ? e.message : '録画を開始できません', 'error'),
                      )
                  "
                >
                  今すぐ録画
                </button>
                <a
                  :href="`https://www.youtube.com/watch?v=${video.youtube_video_id}`"
                  target="_blank"
                  rel="noreferrer"
                  >YouTube で開く ↗</a
                >
              </div>
            </details>
          </div>
        </article>
      </div>
    </section>
  </section>
</template>
