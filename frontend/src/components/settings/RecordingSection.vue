<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>

<template>
  <section class="card">
    <h2>録画の実効設定</h2>
    <pre class="summary-text">{{ JSON.stringify(ctx.recordingSettings, null, 2) }}</pre>
  </section>
</template>
