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
        <h1>ストレージ</h1>
        <p>保存先の到達性、空き容量、しきい値を管理</p>
      </div>
      <button class="button primary" @click="ctx.openStorage()"><Icon name="add" />保存先を追加</button>
    </header>
    <div v-if="!ctx.storages.length" class="empty-state">
      保存先がありません。<br />まず保存先を登録してください。<br />
      <button class="button primary" style="margin-top: 12px" @click="ctx.openStorage()">保存先を登録</button>
    </div>
    <div class="list">
      <article v-for="storage in ctx.storages" :key="storage.id" class="card storage-card">
        <span class="brand-mark"><Icon name="storage" /></span>
        <div class="item-main">
          <div class="card-header">
            <div>
              <b>{{ storage.name }}</b>
              <span v-if="storage.is_default" class="badge info">既定</span>
              <small>{{ storage.root_path }}</small>
            </div>
            <span
              class="badge"
              :class="
                ctx.storageUsages.get(storage.id)?.reachable && ctx.storageUsages.get(storage.id)?.writable
                  ? 'success'
                  : 'danger'
              "
              >{{
                !ctx.storageUsages.get(storage.id)?.reachable
                  ? '到達不能'
                  : ctx.storageUsages.get(storage.id)?.writable
                    ? '書込可能'
                    : '書込不可'
              }}</span
            >
          </div>
          <div v-if="ctx.storageUsages.get(storage.id)" class="progress">
            <i
              :style="{
                width: ctx.storageUsages.get(storage.id)?.total_bytes
                  ? `${Math.min(100, ((ctx.storageUsages.get(storage.id)?.used_bytes || 0) / (ctx.storageUsages.get(storage.id)?.total_bytes || 1)) * 100)}%`
                  : '0%',
              }"
            >
            </i>
          </div>
          <div class="storage-legend">
            <span>全体 {{ ctx.formatBytes(ctx.storageUsages.get(storage.id)?.total_bytes) }}</span>
            <span>
              <i class="storage-mark system"> </i>使用
              {{ ctx.formatBytes(ctx.storageUsages.get(storage.id)?.used_bytes) }}</span
            >
            <span>
              <i class="storage-mark"> </i>本アプリ
              {{ ctx.formatBytes(ctx.storageUsages.get(storage.id)?.app_bytes) }}</span
            >
            <span>空き {{ ctx.formatBytes(ctx.storageUsages.get(storage.id)?.free_bytes) }}</span>
            <span>{{
              storage.minimum_free_bytes || storage.warning_free_bytes
                ? `最低 ${ctx.formatBytes(storage.minimum_free_bytes)} · 警告 ${ctx.formatBytes(storage.warning_free_bytes)}`
                : 'しきい値: 未設定'
            }}</span>
          </div>
          <span v-if="ctx.storageUsages.get(storage.id)?.critical" class="badge danger"
            >最低空き容量未満</span
          >
          <span v-else-if="ctx.storageUsages.get(storage.id)?.warning" class="badge warning"
            >空き容量が警告値未満</span
          >
        </div>
        <div class="actions">
          <button class="button secondary small" @click="ctx.openStorage(storage)">編集</button>
          <details class="menu">
            <summary class="button small" aria-label="保存先操作"><Icon name="more" /></summary>
            <div class="menu-items">
              <button @click="ctx.changeEnabled(storage)">{{ storage.enabled ? '無効化' : '有効化' }}</button>
              <button class="destructive" @click="ctx.deleteStorage(storage)">削除</button>
            </div>
          </details>
        </div>
      </article>
    </div>
  </section>
</template>
