<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>

<template>
  <section class="card">
    <h2>要約</h2>
    <p>生成時、文字起こしテキストが設定した外部プロバイダーへ送信されます。</p>
    <div class="form-grid">
      <label class="check-field"
        ><input v-model="ctx.summarySettings.summary_enabled" type="checkbox" />有効</label
      >
      <label class="field">モデル<input v-model="ctx.summarySettings.openrouter_model" /></label>
      <label class="field"
        >API キー（変更時のみ入力）<input
          v-model="ctx.summarySettings.openrouter_api_key"
          type="password"
          autocomplete="new-password"
          placeholder="保存済みの値は非表示"
      /></label>
      <label class="field">API ベース URL<input v-model="ctx.summarySettings.openrouter_base_url" /></label>
      <label class="field"
        >チャンク長（分）<input
          v-model.number="ctx.summarySettings.summary_chunk_minutes"
          type="number"
          min="1"
      /></label>
    </div>
    <div class="settings-actions">
      <button class="button primary" @click="ctx.saveSettings('summary')">要約設定を保存</button>
    </div>
  </section>
</template>
