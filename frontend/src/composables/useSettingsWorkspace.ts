import { api } from '../api/resources'
import { request } from '../api/client'
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

  async function saveSettings(kind: 'transcription' | 'summary') {
    const payload = kind === 'transcription' ? state.transcription.value : state.summarySettings.value
    try {
      const result = await request<Record<string, unknown>>(`/settings/${kind}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      if (kind === 'transcription') state.transcription.value = result
      else state.summarySettings.value = { ...result, openrouter_api_key: '' }
      state.push(`${kind === 'summary' ? '要約' : '文字起こし'}設定を保存しました`)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '設定を保存できません', 'error')
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
  }
}
