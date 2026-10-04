import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ChannelFormDialog from './ChannelFormDialog.vue'
import type { Channel } from '../api/types'
import { HttpError } from '../api/client'
const channel: Channel = {
  id: '42',
  youtube_channel_id: 'UCexample',
  url: 'https://youtube.com/@sample',
  name: 'Sample',
  enabled: true,
  poll_interval: '30',
  storage_id: '5',
  fallback_storage_id: null,
  retention_days: 30,
  deletion_grace_days: 3,
  quality: 'best',
  live_quality: '',
  archive_quality: 'best',
  cookie_profile: null,
  auto_protect_private: true,
  auto_protect_deleted: true,
  auto_protect_edited: true,
  edit_detection_mode: 'standard',
  always_protect: false,
  backfill_limit: 0,
  live_playback_enabled: false,
  live_from_start: true,
  live_playback_limit_bytes: 5 * 1024 ** 3,
  chat_enabled: true,
  archived: false,
  next_check_at: null,
  last_checked_at: null,
  last_success_at: null,
  last_error: null,
  consecutive_failures: 0,
  has_avatar: false,
  recording_count: 4,
  recording_now_count: 0,
  upcoming_count: 1,
  failed_count: 0,
  monitor_state: 'ok',
}
vi.mock('../api/resources', () => ({
  api: {
    storages: vi.fn().mockResolvedValue([]),
    qualityPresets: vi.fn().mockResolvedValue([
      { id: 'original', label: '自動', selector: '', description: '自動選択' },
      { id: 'av1', label: 'AV1 優先', selector: 'bv*[vcodec^=av01]+ba', description: '高画質' },
    ]),
    patchChannel: vi.fn().mockResolvedValue({}),
    addChannel: vi.fn().mockResolvedValue({}),
  },
}))
import { api } from '../api/resources'
beforeEach(() => {
  vi.mocked(api.storages).mockResolvedValue([])
  vi.mocked(api.qualityPresets).mockResolvedValue([
    { id: 'original', label: '自動', selector: '', description: '自動選択' },
    { id: 'av1', label: 'AV1 優先', selector: 'bv*[vcodec^=av01]+ba', description: '高画質' },
  ])
  vi.mocked(api.addChannel).mockResolvedValue(channel)
  vi.mocked(api.patchChannel).mockResolvedValue(channel)
})
describe('ChannelFormDialog', () => {
  it('sends changed fields only and sends permanent retention as null', async () => {
    const wrapper = mount(ChannelFormDialog, { props: { channel } })
    await flushPromises()
    await wrapper.get('#retention').setValue('permanent')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.patchChannel).toHaveBeenCalledWith('42', { retention_days: null }, false)
    expect(wrapper.emitted('saved')).toBeTruthy()
  })
  it('converts GB input to bytes when changed', async () => {
    const wrapper = mount(ChannelFormDialog, { props: { channel } })
    await flushPromises()
    await wrapper.get('#live-limit').setValue('2.5')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.patchChannel).toHaveBeenCalledWith('42', { live_playback_limit_bytes: 2.5 * 1024 ** 3 }, false)
  })
  it('defaults live-from-start on and disables it while live playback is enabled', async () => {
    const wrapper = mount(ChannelFormDialog, { props: { channel } })
    await flushPromises()
    const liveFromStart = wrapper
      .findAll('label')
      .find((label) => label.text().includes('配信の先頭から録画する'))!
    const livePlayback = wrapper
      .findAll('label')
      .find((label) => label.text().includes('追っかけ視聴を有効にする'))!
    expect((liveFromStart.get('input').element as HTMLInputElement).checked).toBe(true)
    await liveFromStart.get('input').setValue(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.patchChannel).toHaveBeenCalledWith('42', { live_from_start: false }, false)
    await livePlayback.get('input').setValue(true)
    expect((liveFromStart.get('input').element as HTMLInputElement).disabled).toBe(true)
  })

  it('creates a channel with a preset selector while showing its explanation', async () => {
    const wrapper = mount(ChannelFormDialog)
    await flushPromises()
    await wrapper.get('#channel-url').setValue('@example')
    await wrapper.get('#archive-quality').setValue('av1')
    expect(wrapper.text()).toContain('高画質')
    expect(wrapper.text()).not.toContain('bv*[vcodec^=av01]+ba')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.addChannel).toHaveBeenCalledWith(
      expect.objectContaining({ url: '@example', archive_quality: 'bv*[vcodec^=av01]+ba' }),
    )
  })

  it('shows API field errors directly under the URL input', async () => {
    vi.mocked(api.addChannel).mockRejectedValue(
      new HttpError('入力を確認してください', 422, { url: 'チャンネル URL を確認してください' }),
    )
    const wrapper = mount(ChannelFormDialog)
    await flushPromises()
    await wrapper.get('#channel-url').setValue('bad')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('.field small').text()).toBe('チャンネル URL を確認してください')
    expect(wrapper.text()).not.toContain('{"error"')
  })
})
