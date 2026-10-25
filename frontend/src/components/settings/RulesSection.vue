<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useWorkspaceContext } from '../../composables/workspaceContext'
import type { RecordingRule } from '../../api/types'
import RuleFormDialog from '../RuleFormDialog.vue'
import EmptyState from '../ui/EmptyState.vue'

const ctx = reactive(useWorkspaceContext())
const open = ref(false)
const editing = ref<RecordingRule | null>(null)
function addRule() {
  editing.value = null
  open.value = true
}
function editRule(rule: RecordingRule) {
  editing.value = rule
  open.value = true
}
</script>

<template>
  <section class="card">
    <header class="card-header">
      <div>
        <h2>ルール録画</h2>
        <p>タイトルや説明に一致した配信を録画・除外します。</p>
      </div>
      <button class="button secondary" @click="addRule">ルールを追加</button>
    </header>
    <div v-if="ctx.rules.length" class="rules-list">
      <article v-for="rule in ctx.rules" :key="rule.id" class="rule-row">
        <div>
          <small>名前</small><b>{{ rule.name }}</b>
        </div>
        <div>
          <small>種別</small><span>{{ rule.is_exclusion ? '除外' : '録画' }}</span>
        </div>
        <div class="rule-condition">
          <small>条件</small>
          <span
            >{{
              rule.target === 'title' ? 'タイトル' : rule.target === 'description' ? '説明' : 'タイトル・説明'
            }}
            · {{ rule.match_mode === 'regex' ? '正規表現' : 'キーワード' }} · {{ rule.pattern }}</span
          >
        </div>
        <div>
          <small>対象チャンネル</small
          ><span>{{ ctx.channels.find((channel) => channel.id === rule.channel_id)?.name || 'すべて' }}</span>
        </div>
        <div>
          <small>上書き内容</small
          ><span
            >{{
              rule.storage_id
                ? ctx.storages.find((item) => item.id === rule.storage_id)?.name
                : 'チャンネル設定'
            }}
            · {{ rule.quality || '既定画質' }}</span
          >
        </div>
        <label class="check-field"
          ><input :checked="rule.enabled" type="checkbox" @change="ctx.setRuleEnabled(rule)" />有効</label
        >
        <div class="actions">
          <button class="button secondary small" @click="editRule(rule)">編集</button>
          <button class="button danger small" @click="ctx.deleteRule(rule)">削除</button>
        </div>
      </article>
    </div>
    <EmptyState
      v-else
      title="録画ルールはありません"
      description="条件を追加すると配信を自動で録画できます。"
    />
    <RuleFormDialog :open="open" :rule="editing" @close="open = false" @saved="ctx.load(true)" />
  </section>
</template>
