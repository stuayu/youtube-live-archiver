<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>

<template>
  <section class="card">
    <h2>フォーマット確認</h2>
    <div class="toolbar">
      <label class="field grow">YouTube 動画 ID<input v-model="ctx.formatId" placeholder="動画 ID" /></label>
      <button class="button secondary" @click="ctx.loadFormats">形式を確認</button>
    </div>
    <pre v-if="ctx.formats" class="summary-text">{{ ctx.formats }}</pre>
  </section>
</template>
