import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Pagination from './Pagination.vue'
describe('Pagination', () => {
  it('shows page range and emits offset', async () => {
    const wrapper = mount(Pagination, { props: { offset: 30, limit: 30, total: 67 } })
    expect(wrapper.text()).toContain('31–60 / 全 67 件')
    await wrapper.get('button:last-child').trigger('click')
    expect(wrapper.emitted('change')?.[0]).toEqual([60])
  })
  it('disables previous at first page', () => {
    const wrapper = mount(Pagination, { props: { offset: 0, limit: 30, total: 10 } })
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  })
})
