import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ManualVideoDialog from './ManualVideoDialog.vue'

const { previewManualVideo } = vi.hoisted(() => ({ previewManualVideo: vi.fn() }))
vi.mock('../api/resources', () => ({ api: { previewManualVideo, addManualVideo: vi.fn() } }))

describe('ManualVideoDialog', () => {
  beforeEach(() => previewManualVideo.mockReset())

  it('shows URL validation and preview, and closes on Escape', async () => {
    const wrapper = mount(ManualVideoDialog, { props: { storages: [] }, attachTo: document.body })
    await wrapper.get('form').trigger('submit')
    expect(wrapper.find('.inline-error').text()).toContain('URL')
    previewManualVideo.mockResolvedValue({
      video_id: 'abcdefghijk',
      title: '予定タイトル',
      thumbnail: null,
      channel: { youtube_channel_id: 'UC123', name: 'チャンネル', registered: false, monitored: false },
      live_status: 'is_upcoming',
      scheduled_start_at: null,
      duration: null,
      action: 'wait_live',
      action_label: '予定',
      existing: null,
    })
    document.body.append(wrapper.element)
    await wrapper.get('input').setValue('abcdefghijk')
    await wrapper.get('form').trigger('submit')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('予定タイトル')
    expect(wrapper.text()).toContain('監視しないチャンネルとして登録します')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.emitted('close')?.length).toBe(1)
    wrapper.unmount()
  })

  it('uses shared dialog structure, disables add before preview, and replaces a failed image once', async () => {
    const wrapper = mount(ManualVideoDialog, { props: { storages: [] }, attachTo: document.body })
    expect(wrapper.find('.dialog-header').exists()).toBe(true)
    expect(wrapper.find('.dialog-body').exists()).toBe(true)
    expect(wrapper.find('.dialog-footer').exists()).toBe(true)
    expect((wrapper.get('.dialog-footer button.primary').element as HTMLButtonElement).disabled).toBe(true)
    previewManualVideo.mockResolvedValue({
      video_id: 'abcdefghijk',
      title: 'title',
      thumbnail: '/thumb',
      channel: { youtube_channel_id: 'UC123', name: 'channel', registered: true, monitored: true },
      live_status: 'was_live',
      scheduled_start_at: null,
      duration: null,
      action: 'download_archive',
      action_label: 'archive',
      existing: null,
    })
    await wrapper.get('input').setValue('abcdefghijk')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const image = wrapper.get('img')
    await image.trigger('error')
    expect(image.attributes('src')).toBe('/icon.svg')
    await image.trigger('error')
    expect(image.attributes('src')).toBe('/icon.svg')
    wrapper.unmount()
  })
})
