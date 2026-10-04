import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Dialog from './Dialog.vue'

describe('Dialog', () => {
  it('exposes an accessible modal and emits close on Escape', async () => {
    const wrapper = mount(Dialog, { props: { title: '削除確認' } })
    expect(wrapper.get('[role="dialog"]').attributes('aria-modal')).toBe('true')
    expect(wrapper.get('[role="dialog"]').attributes('aria-label')).toBe('削除確認')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})
