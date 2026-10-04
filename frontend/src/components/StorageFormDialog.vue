<script setup lang="ts">
import Dialog from './ui/Dialog.vue'

export type StorageFormData = {
  name: string
  root_path: string
  minimumGb: number
  warningGb: number
  enabled: boolean
}

const props = defineProps<{ modelValue: StorageFormData; editing: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: StorageFormData]
  close: []
  save: []
}>()

function update<K extends keyof StorageFormData>(key: K, value: StorageFormData[K]) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function numberValue(event: Event): number {
  const value = Number((event.target as HTMLInputElement).value)
  return Number.isFinite(value) && value >= 0 ? value : 0
}
</script>

<template>
  <Dialog :title="editing ? '保存先を編集' : '保存先を追加'" @close="emit('close')">
    <div class="form-grid">
      <label class="field">
        保存先名
        <input
          required
          :value="modelValue.name"
          @input="update('name', ($event.target as HTMLInputElement).value)"
        />
      </label>
      <label class="field">
        保存先パス
        <input
          required
          :value="modelValue.root_path"
          placeholder="/mnt/archive または D:\\archive"
          @input="update('root_path', ($event.target as HTMLInputElement).value)"
        />
        <small>macOS / Linux: /mnt/archive · Windows: D:\\archive · NAS: \\\\server\\share</small>
      </label>
      <label class="field">
        最低空き容量（GB）
        <input
          type="number"
          min="0"
          step="0.1"
          :value="modelValue.minimumGb"
          @input="update('minimumGb', numberValue($event))"
        />
      </label>
      <label class="field">
        警告空き容量（GB）
        <input
          type="number"
          min="0"
          step="0.1"
          :value="modelValue.warningGb"
          @input="update('warningGb', numberValue($event))"
        />
      </label>
      <label class="check-field">
        <input
          type="checkbox"
          :checked="modelValue.enabled"
          @change="update('enabled', ($event.target as HTMLInputElement).checked)"
        />
        有効
      </label>
    </div>
    <template #footer>
      <button class="button secondary" @click="emit('close')">キャンセル</button>
      <button class="button primary" @click="emit('save')">保存</button>
    </template>
  </Dialog>
</template>
