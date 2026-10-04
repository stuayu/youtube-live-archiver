import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { useToast } from '../../composables/useToast'
import Toasts from './Toasts.vue'

describe('Toasts', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useToast().toasts.value = []
  })

  it('shows feedback and allows dismissing it', async () => {
    const wrapper = mount(Toasts)
    useToast().push('保存しました')
    await nextTick()
    expect(wrapper.text()).toContain('保存しました')
    await wrapper.get('[aria-label="通知を閉じる"]').trigger('click')
    await nextTick()
    expect(wrapper.text()).not.toContain('保存しました')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('dismisses feedback after five seconds', async () => {
    const wrapper = mount(Toasts)
    useToast().push('取得に失敗しました', 'error')
    vi.advanceTimersByTime(5000)
    await nextTick()
    expect(wrapper.text()).not.toContain('取得に失敗しました')
    wrapper.unmount()
    vi.useRealTimers()
  })
})
