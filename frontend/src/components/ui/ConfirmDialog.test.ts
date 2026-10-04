import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ConfirmDialog from './ConfirmDialog.vue'

describe('ConfirmDialog', () => {
  it('describes the action and emits cancel or confirm distinctly', async () => {
    const wrapper = mount(ConfirmDialog, {
      props: { title: '録画を削除', body: 'ファイルを削除します。元に戻せません。' },
    })
    expect(wrapper.get('[role="dialog"]').text()).toContain('元に戻せません')
    await wrapper.get('button.secondary').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    await wrapper.get('button.danger').trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
  })
})
