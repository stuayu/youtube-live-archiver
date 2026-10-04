<script setup lang="ts">
import { computed, provide, reactive, ref } from 'vue'
import type { Component } from 'vue'
import ActivityView from './views/ActivityView.vue'
import AnalysisView from './views/AnalysisView.vue'
import ArchiveView from './views/ArchiveView.vue'
import ChannelsView from './views/ChannelsView.vue'
import HomeView from './views/HomeView.vue'
import SearchView from './views/SearchView.vue'
import SettingsView from './views/SettingsView.vue'
import StoragesView from './views/StoragesView.vue'
import UpcomingView from './views/UpcomingView.vue'
import ChannelFormDialog from './components/ChannelFormDialog.vue'
import StorageFormDialog from './components/StorageFormDialog.vue'
import Icon from './components/ui/Icon.vue'
import ConfirmDialog from './components/ui/ConfirmDialog.vue'
import Toasts from './components/ui/Toasts.vue'
import PlayerDialog from './components/PlayerDialog.vue'
import { useWorkspace } from './composables/useWorkspace'
import { workspaceKey } from './composables/workspaceContext'
import type { Tab } from './composables/useQueryState'
import { databaseUnavailable } from './api/client'

const workspace = useWorkspace()
provide(workspaceKey, workspace)
const ctx = reactive(workspace)
const moreMenuOpen = ref(false)
const views: Record<Tab, Component> = {
  home: HomeView,
  channels: ChannelsView,
  upcoming: UpcomingView,
  videos: ArchiveView,
  search: SearchView,
  storages: StoragesView,
  analysis: AnalysisView,
  activity: ActivityView,
  settings: SettingsView,
}
const activeView = computed(() => views[ctx.tab])
function selectMoreTab(id: Tab) {
  ctx.setTab(id)
  moreMenuOpen.value = false
}
function playerUpdated() {
  ctx.playerVideo = null
  ctx.load(true)
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">▶</span>YouTube Live Archiver</div>
      <nav class="side-nav" aria-label="メインメニュー">
        <button
          v-for="item in ctx.mainTabs"
          :key="item.id"
          class="nav-item"
          :class="{ active: ctx.tab === item.id }"
          @click="ctx.setTab(item.id)"
        >
          <Icon :name="item.icon" />{{ item.label }}
        </button>
      </nav>
    </aside>

    <main class="shell-main">
      <div class="mobile-top">
        <div class="brand"><span class="brand-mark">▶</span>Live Archiver</div>
        <span class="last-updated">最終更新 {{ ctx.lastUpdated || '—' }}</span>
        <button class="icon-button" aria-label="更新" :disabled="ctx.refreshing" @click="ctx.refresh">
          <Icon name="refresh" />
        </button>
      </div>
      <header class="topbar">
        <div class="last-updated">最終更新 {{ ctx.lastUpdated || '—' }}</div>
        <button class="button secondary" :disabled="ctx.refreshing" @click="ctx.refresh">
          <Icon name="refresh" />{{ ctx.refreshing ? '更新中…' : '更新' }}
        </button>
      </header>
      <div v-if="ctx.refreshing" class="loading-strip" />
      <div v-if="databaseUnavailable" class="attention error db-unavailable" role="alert">
        backend はデータベースに接続できていません
      </div>
      <div class="content"><component :is="activeView" /></div>
    </main>

    <nav class="mobile-nav" aria-label="メインメニュー">
      <button
        v-for="item in ctx.mobileTabs"
        :key="item.id"
        class="mobile-tab"
        :class="{ active: ctx.tab === item.id }"
        @click="ctx.setTab(item.id)"
      >
        <Icon :name="item.icon" />{{ item.label }}
      </button>
      <div class="mobile-tab-more">
        <button
          class="mobile-tab"
          :class="{ active: ctx.moreTabs.some((item) => item.id === ctx.tab) }"
          @click="moreMenuOpen = !moreMenuOpen"
        >
          <Icon name="more" />その他
        </button>
        <div v-if="moreMenuOpen" class="mobile-more-menu">
          <button v-for="item in ctx.moreTabs" :key="item.id" @click="selectMoreTab(item.id)">
            <Icon :name="item.icon" />{{ item.label }}
          </button>
        </div>
      </div>
    </nav>

    <Toasts />
    <ChannelFormDialog
      v-if="ctx.editingChannel !== undefined"
      :channel="ctx.editingChannel"
      @close="ctx.editingChannel = undefined"
      @saved="ctx.saveChannel"
    />
    <PlayerDialog
      v-if="ctx.playerVideo"
      :video="ctx.playerVideo"
      :live="ctx.playerLive"
      :initial-time="ctx.playerStart"
      @close="ctx.playerVideo = null"
      @updated="playerUpdated"
    />
    <ConfirmDialog
      v-if="ctx.confirmState"
      :title="ctx.confirmState.title"
      :body="ctx.confirmState.body"
      @close="ctx.confirmState = null"
      @confirm="ctx.executeConfirm"
    />
    <StorageFormDialog
      v-if="ctx.showStorageDialog"
      v-model="ctx.storageForm"
      :editing="Boolean(ctx.editingStorage)"
      @save="ctx.saveStorage"
      @close="ctx.showStorageDialog = false"
    />
  </div>
</template>
