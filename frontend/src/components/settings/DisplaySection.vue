<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>

<template>
  <section class="card">
    <h2>表示</h2>
    <label class="field" style="max-width: 260px"
      >テーマ<select v-model="ctx.theme">
        <option value="auto">自動（OS 設定）</option>
        <option value="light">ライト</option>
        <option value="dark">ダーク</option>
      </select></label
    >
  </section>
</template>
