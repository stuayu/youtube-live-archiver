import { api } from '../api/resources'
import { HttpError, request } from '../api/client'
import type { RecordingRule } from '../api/types'
import type { useWorkspaceState } from './useWorkspaceState'

type WorkspaceState = ReturnType<typeof useWorkspaceState>
type ConfirmAction = (title: string, body: string, run: () => Promise<void>) => void
type LoadData = (silent?: boolean) => Promise<void>

export function useSettingsWorkspace(state: WorkspaceState, load: LoadData, confirm: ConfirmAction) {
  function createRule() {
    state.ruleForm.value = {
      name: '',
      pattern: '',
      target: 'both',
      match_mode: 'keyword',
      is_exclusion: false,
      case_sensitive: false,
      channel_id: null,
      enabled: true,
      storage_id: null,
      retention_days: null,
      quality: null,
      edit_detection_mode: null,
      priority: 100,
    }
    state.ruleEditing.value = undefined
  }

  async function setRuleEnabled(rule: RecordingRule) {
    try {
      await api.saveRule(
        {
          name: rule.name,
          pattern: rule.pattern,
          target: rule.target,
          match_mode: rule.match_mode,
          is_exclusion: rule.is_exclusion,
          case_sensitive: rule.case_sensitive,
          channel_id: rule.channel_id,
          enabled: !rule.enabled,
          storage_id: rule.storage_id,
          retention_days: rule.retention_days,
          quality: rule.quality,
          edit_detection_mode: rule.edit_detection_mode,
          priority: rule.priority,
        },
        rule.id,
      )
      await load(true)
      state.push(rule.enabled ? 'ルールを無効にしました' : 'ルールを有効にしました')
    } catch (error) {
      state.push(error instanceof Error ? error.message : '更新できません', 'error')
    }
  }

  async function loadFormats() {
    try {
      const result = await request<{ formats: string }>(`/formats/${state.formatId.value.trim()}`)
      state.formats.value = result.formats
    } catch (error) {
      state.push(error instanceof Error ? error.message : '形式を確認できません', 'error')
    }
  }

  async function saveRule() {
    try {
      await api.saveRule(state.ruleForm.value, state.ruleEditing.value)
      state.ruleForm.value = {
        name: '',
        pattern: '',
        target: 'both',
        match_mode: 'keyword',
        enabled: true,
        priority: 100,
      }
      state.ruleEditing.value = undefined
      state.push('録画ルールを保存しました')
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : 'ルールを保存できません', 'error')
    }
  }

  function editRule(rule: RecordingRule) {
    state.ruleForm.value = { ...rule }
    state.ruleEditing.value = rule.id
  }

  function deleteRule(rule: RecordingRule) {
    confirm('録画ルールを削除', `「${rule.name}」を削除します。`, async () => {
      await api.deleteRule(rule.id)
      state.push('ルールを削除しました')
    })
  }

  async function previewRule() {
    try {
      state.rulePreview.value = await api.previewRule(state.ruleForm.value)
    } catch (error) {
      state.push(error instanceof Error ? error.message : 'プレビューに失敗しました', 'error')
    }
  }

  function summaryPayload(): Record<string, unknown> {
    const payload: Record<string, unknown> = { ...state.summarySettings.value }
    // 表示専用の項目は送らない。API キーは入力したときだけ送る（空なら登録済みのキーを保持）。
    delete payload.openrouter_api_key_set
    if (!payload.openrouter_api_key) delete payload.openrouter_api_key
    return payload
  }

  function summaryError(error: unknown, fallback: string) {
    const message = error instanceof Error ? error.message : fallback
    state.summaryFeedback.value = {
      tone: 'error',
      message,
      fields: error instanceof HttpError ? error.fields : {},
    }
    state.push(message, 'error')
  }

  async function saveSettings(kind: 'transcription' | 'summary') {
    const payload: Record<string, unknown> =
      kind === 'transcription' ? { ...state.transcription.value } : summaryPayload()
    if (kind === 'summary') {
      state.summaryFeedback.value = {
        tone: 'info',
        message: '接続設定を変えた場合はプロバイダーにテスト送信して確認しています…',
        fields: {},
      }
    }
    try {
      const result = await request<Record<string, unknown>>(`/settings/${kind}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      if (kind === 'transcription') state.transcription.value = result
      else {
        state.summarySettings.value = { ...result, openrouter_api_key: '' }
        state.summaryFeedback.value = { tone: 'ok', message: '要約設定を保存しました', fields: {} }
      }
      state.push(`${kind === 'summary' ? '要約' : '文字起こし'}設定を保存しました`)
    } catch (error) {
      if (kind === 'summary') summaryError(error, '設定を保存できません')
      else state.push(error instanceof Error ? error.message : '設定を保存できません', 'error')
    }
  }

  async function testSummaryProvider() {
    state.summaryFeedback.value = {
      tone: 'info',
      message: 'テスト用の文字起こしを送信しています…',
      fields: {},
    }
    try {
      const result = await request<{ model?: string; latency_ms?: number; sample?: string }>(
        '/settings/summary/test',
        { method: 'POST', body: JSON.stringify(summaryPayload()) },
      )
      const seconds = ((result.latency_ms ?? 0) / 1000).toFixed(1)
      state.summaryFeedback.value = {
        tone: 'ok',
        message: `接続できました（${result.model ?? 'モデル'}・${seconds} 秒）。応答例: ${result.sample || '（空）'}`,
        fields: {},
      }
    } catch (error) {
      summaryError(error, '接続テストに失敗しました')
    }
  }

  return {
    createRule,
    setRuleEnabled,
    loadFormats,
    saveRule,
    editRule,
    deleteRule,
    previewRule,
    saveSettings,
    testSummaryProvider,
  }
}
