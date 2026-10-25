import { computed } from 'vue'
import { api } from '../api/resources'
import type { Storage } from '../api/types'
import { bytesToGb, gbToBytes } from '../lib/format'
import type { useWorkspaceState } from './useWorkspaceState'

type WorkspaceState = ReturnType<typeof useWorkspaceState>
type ConfirmAction = (title: string, body: string, run: () => Promise<void>) => void
type LoadData = (silent?: boolean) => Promise<void>

export function useStorageWorkspace(state: WorkspaceState, load: LoadData, confirm: ConfirmAction) {
  function openStorage(storage?: Storage) {
    state.editingStorage.value = storage ?? null
    state.storageForm.value = storage
      ? {
          name: storage.name,
          root_path: storage.root_path,
          minimumGb: bytesToGb(storage.minimum_free_bytes) as number,
          warningGb: bytesToGb(storage.warning_free_bytes) as number,
          enabled: storage.enabled,
        }
      : { name: '', root_path: '', minimumGb: 0, warningGb: 0, enabled: true }
    state.showStorageDialog.value = true
  }

  async function saveStorage() {
    const body = {
      name: state.storageForm.value.name.trim(),
      root_path: state.storageForm.value.root_path.trim(),
      enabled: state.storageForm.value.enabled,
      minimum_free_bytes: gbToBytes(state.storageForm.value.minimumGb) ?? 0,
      warning_free_bytes: gbToBytes(state.storageForm.value.warningGb) ?? 0,
    }
    try {
      if (state.editingStorage.value) await api.patchStorage(state.editingStorage.value.id, body)
      else await api.addStorage(body)
      state.showStorageDialog.value = false
      state.push('保存先を保存しました')
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '保存先を保存できません', 'error')
    }
  }

  function deleteStorage(storage: Storage) {
    confirm(
      '保存先を削除',
      `${storage.name} を削除します。参照中の場合は API が削除を拒否し、理由を表示します。`,
      async () => {
        await api.deleteStorage(storage.id)
        state.push('保存先を削除しました')
      },
    )
  }

  async function changeEnabled(storage: Storage) {
    try {
      await api.patchStorage(storage.id, { enabled: !storage.enabled })
      state.push(storage.enabled ? '保存先を無効にしました' : '保存先を有効にしました')
      await load(true)
    } catch (error) {
      state.push(error instanceof Error ? error.message : '状態を変更できません', 'error')
    }
  }

  const storageUsages = computed(() => new Map(state.usage.value.map((item) => [item.id, item])))

  return { openStorage, saveStorage, deleteStorage, changeEnabled, storageUsages }
}
