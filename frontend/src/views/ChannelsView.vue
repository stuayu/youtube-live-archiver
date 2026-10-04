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
        <h1>チャンネル</h1>
        <p>監視状態、次回確認、保存設定を管理</p>
      </div>
      <button class="button primary" @click="ctx.editingChannel = null">
        <Icon name="add" />チャンネルを追加
      </button>
    </header>
    <div class="toolbar">
      <label class="field grow"
        >検索<input v-model="ctx.channelSearch" placeholder="チャンネル名・ID" />
      </label>
      <label class="field"
        >監視状態<select v-model="ctx.channelState">
          <option value="all">すべて</option>
          <option value="ok">監視中</option>
          <option value="recording">録画中</option>
          <option value="error">エラー</option>
          <option value="paused">停止中</option>
          <option value="pending">確認待ち</option>
        </select>
      </label>
      <label class="field"
        >並び順<select v-model="ctx.channelSort">
          <option value="name">名前</option>
          <option value="next">次回確認</option>
          <option value="recordings">録画件数</option>
        </select>
      </label>
    </div>
    <div v-if="ctx.loading && !ctx.channels.length" class="list">
      <div v-for="n in 4" :key="n" class="skeleton"></div>
    </div>
    <div v-else-if="!ctx.filteredChannels.length" class="empty-state">
      チャンネルはありません。<br />
      <button class="button primary" style="margin-top: 12px" @click="ctx.editingChannel = null">
        チャンネルを追加
      </button>
    </div>
    <div class="list">
      <article v-for="channel in ctx.filteredChannels" :key="channel.id" class="card channel-card">
        <img
          class="avatar"
          :src="channel.has_avatar ? ctx.apiUrl(`/channels/${channel.id}/avatar`) : '/icon.svg'"
          :alt="`${channel.name} のアイコン`"
        />
        <div class="item-main">
          <div class="item-title">{{ channel.name }}</div>
          <div class="meta">
            <span class="badge" :class="ctx.monitorStatus(channel.monitor_state).tone">{{
              ctx.monitorStatus(channel.monitor_state).label
            }}</span>
            <span class="labeled-value"
              ><small>次回確認</small>{{ ctx.relativeTime(channel.next_check_at) }}</span
            >
            <span class="labeled-value"
              ><small>最終成功</small>{{ ctx.relativeTime(channel.last_success_at) }}</span
            >
            <span class="labeled-value"
              ><small>保存先</small
              >{{ ctx.storages.find((s) => s.id === channel.storage_id)?.name || '既定' }}</span
            >
            <span class="labeled-value"
              ><small>保存期間</small>{{ ctx.formatRetention(channel.retention_days) }}</span
            >
            <a class="labeled-value" :href="`?tab=videos&channel=${channel.id}`"
              ><small>録画</small>{{ channel.recording_count }}</a
            >
            <a class="labeled-value" :href="`?tab=upcoming&channel=${channel.id}`"
              ><small>予定</small>{{ channel.upcoming_count ?? 0 }}</a
            >
            <a class="labeled-value" :href="`?tab=videos&channel=${channel.id}&vstatus=RECORDING_FAILED`"
              ><small>失敗</small>{{ channel.failed_count ?? 0 }}</a
            >
          </div>
          <div v-if="channel.last_error" class="inline-error">
            {{ ctx.monitorErrorSummary(channel.last_error) }}
            <details>
              <summary>詳細</summary>
              {{ channel.last_error }}
            </details>
            <small
              >連続失敗 {{ channel.consecutive_failures }} 回 · 次回
              {{ ctx.relativeTime(channel.next_check_at) }}</small
            >
          </div>
        </div>
        <div class="actions">
          <button class="button primary small" @click="ctx.editingChannel = channel">編集</button>
          <details class="menu">
            <summary class="button small" aria-label="チャンネル操作"><Icon name="more" /></summary>
            <div class="menu-items">
              <button @click="ctx.checkChannel(channel)">今すぐ確認</button>
              <button @click="ctx.refreshChannel(channel)">情報更新</button>
              <a :href="channel.url" target="_blank" rel="noreferrer">YouTube で開く ↗</a>
              <button class="destructive" @click="ctx.removeChannel(channel)">監視解除</button>
            </div>
          </details>
        </div>
      </article>
    </div>
  </section>
</template>
