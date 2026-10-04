<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>
<template>
  <section>
    <header class="page-head">
      <div>
        <h1>全文検索</h1>
        <p>録画済み文字起こしを検索</p>
      </div>
    </header>
    <form class="toolbar search-toolbar" @submit.prevent="ctx.submitSearch">
      <label class="field grow"
        >検索語<input v-model="ctx.searchQuery" placeholder="文字起こし内のキーワード" />
      </label>
      <label class="field"
        >チャンネル<select v-model="ctx.values.channel">
          <option :value="undefined">すべて</option>
          <option v-for="channel in ctx.channels" :key="channel.id" :value="channel.id">
            {{ channel.name }}
          </option>
        </select>
      </label>
      <button class="button primary">検索</button>
    </form>
    <div v-if="ctx.searchResults.results.length" class="meta">
      {{ ctx.searchResults.total }} 件 · {{ ctx.searchResults.offset + 1 }}–{{
        Math.min(ctx.searchResults.offset + 30, ctx.searchResults.total)
      }}
    </div>
    <div v-for="item in ctx.searchResults.results" :key="`${item.video_id}-${item.start}`" class="card">
      <div class="card-header">
        <div>
          <b>{{ item.title }}</b>
          <div class="meta">{{ item.channel_name }} · {{ ctx.formatDuration(item.start) }}</div>
        </div>
        <button class="button secondary" @click="ctx.openSearchResult(item)">この位置から再生</button>
      </div>
      <p>{{ item.text }}</p>
    </div>
    <p v-if="ctx.searchQuery && !ctx.searchResults.total" class="empty-state">検索結果はありません。</p>
    <div class="pager">
      <button class="button secondary" :disabled="ctx.searchOffset === 0" @click="ctx.setSearchPage(-1)">
        前へ
      </button>
      <button
        class="button secondary"
        :disabled="ctx.searchOffset + 30 >= ctx.searchResults.total"
        @click="ctx.setSearchPage(1)"
      >
        次へ
      </button>
    </div>
  </section>
</template>
