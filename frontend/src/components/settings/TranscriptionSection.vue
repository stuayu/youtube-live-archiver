<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>

<template>
  <section class="card">
    <h2>文字起こし</h2>
    <p>Whisper の実行方法を設定します。秘密情報は画面に表示しません。</p>
    <div class="form-grid">
      <label class="check-field"
        ><input v-model="ctx.transcription.transcription_enabled" type="checkbox" />有効</label
      >
      <label class="field">モデル<input v-model="ctx.transcription.whisper_model" /></label>
      <label class="field"
        >デバイス<select v-model="ctx.transcription.whisper_device">
          <option value="auto">自動</option>
          <option value="cpu">CPU</option>
          <option value="cuda">CUDA</option>
        </select></label
      >
      <label class="field"
        >言語<input v-model="ctx.transcription.whisper_language" placeholder="自動判定"
      /></label>
      <label class="field"
        >同時実行数<input
          v-model.number="ctx.transcription.transcription_max_concurrency"
          type="number"
          min="1"
      /></label>
    </div>
    <div class="settings-actions">
      <button class="button primary" @click="ctx.saveSettings('transcription')">文字起こし設定を保存</button>
    </div>
  </section>
</template>
