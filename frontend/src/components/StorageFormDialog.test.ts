import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import StorageFormDialog, { type StorageFormData } from './StorageFormDialog.vue'

describe('StorageFormDialog', () => {
  it('shows labeled GB inputs and emits edited values', async () => {
    const form: StorageFormData = {
      name: 'Archive',
      root_path: '/mnt/archive',
      minimumGb: 4.5,
      warningGb: 12,
      enabled: true,
    }
    const wrapper = mount(StorageFormDialog, { props: { modelValue: form, editing: true } })
    expect(wrapper.text()).toContain('macOS / Linux: /mnt/archive')
    await wrapper.get('input[type="number"]').setValue('5.5')
    expect(wrapper.emitted('update:modelValue')?.[0]?.[0]).toEqual({ ...form, minimumGb: 5.5 })
    await wrapper.get('input[type="checkbox"]').setValue(false)
    expect(wrapper.emitted('update:modelValue')?.[1]?.[0]).toEqual({ ...form, enabled: false })
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('save')).toHaveLength(1)
  })
})
