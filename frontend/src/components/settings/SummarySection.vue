<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useWorkspaceContext } from '../../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
const fieldErrors = computed(() => ctx.summaryFeedback?.fields ?? {})
const busy = computed(() => ctx.summaryFeedback?.tone === 'info')
</script>

<template>
  <section class="card">
    <h2>要約</h2>
    <p>生成時、文字起こしテキストが設定した外部プロバイダーへ送信されます。</p>
    <p class="hint">
      API キー・モデル・API ベース URL を変えて保存すると、短いテスト用の文字起こしをプロバイダーへ送り、
      要約できることを確かめてから保存します。
    </p>
    <div class="form-grid">
      <label class="check-field"
        ><input v-model="ctx.summarySettings.summary_enabled" type="checkbox" />有効</label
      >
      <label class="field"
        >モデル<input
          v-model="ctx.summarySettings.openrouter_model"
          placeholder="例: openai/gpt-4o-mini"
          :aria-invalid="Boolean(fieldErrors.openrouter_model)"
        /><small v-if="fieldErrors.openrouter_model" class="field-error">{{
          fieldErrors.openrouter_model
        }}</small></label
      >
      <label class="field"
        >API キー（{{
          ctx.summarySettings.openrouter_api_key_set ? '登録済み・変更時のみ入力' : '未登録'
        }}）<input
          v-model="ctx.summarySettings.openrouter_api_key"
          type="password"
          autocomplete="new-password"
          :placeholder="ctx.summarySettings.openrouter_api_key_set ? '保存済みの値は非表示' : 'sk-or-...'"
          :aria-invalid="Boolean(fieldErrors.openrouter_api_key)"
        /><small v-if="fieldErrors.openrouter_api_key" class="field-error">{{
          fieldErrors.openrouter_api_key
        }}</small></label
      >
      <label class="field"
        >API ベース URL<input
          v-model="ctx.summarySettings.openrouter_base_url"
          placeholder="https://openrouter.ai/api/v1"
          :aria-invalid="Boolean(fieldErrors.openrouter_base_url)"
        /><small v-if="fieldErrors.openrouter_base_url" class="field-error">{{
          fieldErrors.openrouter_base_url
        }}</small></label
      >
      <label class="field"
        >チャンク長（分）<select v-model.number="ctx.summarySettings.summary_chunk_minutes">
          <option :value="15">15</option>
          <option :value="20">20</option>
          <option :value="30">30</option>
          <option :value="60">60</option>
        </select></label
      >
    </div>
    <p
      v-if="ctx.summaryFeedback"
      class="summary-feedback"
      :class="`tone-${ctx.summaryFeedback.tone}`"
      role="status"
      aria-live="polite"
    >
      {{ ctx.summaryFeedback.message }}
    </p>
    <div class="settings-actions">
      <button class="button" :disabled="busy" @click="ctx.testSummaryProvider()">接続テスト</button>
      <button class="button primary" :disabled="busy" @click="ctx.saveSettings('summary')">
        要約設定を保存
      </button>
    </div>
  </section>
</template>

<style scoped>
.hint {
  color: var(--muted);
  font-size: 0.9em;
}
.field-error {
  color: var(--danger);
}
.summary-feedback {
  margin: 0.75rem 0 0;
  padding: 0.6rem 0.8rem;
  border-radius: var(--radius);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.summary-feedback.tone-ok {
  color: var(--success);
  background: var(--success-soft);
}
.summary-feedback.tone-error {
  color: var(--danger);
  background: var(--danger-soft);
}
.summary-feedback.tone-info {
  color: var(--info);
  background: var(--info-soft);
}
</style>
