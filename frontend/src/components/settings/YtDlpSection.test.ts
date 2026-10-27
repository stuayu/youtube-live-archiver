import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { request } from '../../api/client'
import YtDlpSection from './YtDlpSection.vue'

vi.mock('../../api/client', () => ({ request: vi.fn() }))

const config = {
  channel: 'nightly',
  auto_update: true,
  version: '2026.10.21',
  latest_version: '2026.10.23.233055',
  checked_at: '2026-10-26T00:00:00Z',
  update_available: true,
  update_in_progress: false,
  update_phase: 'idle',
  update_error: null,
  deno_available: false,
  deno_version: null,
  deno_supported: false,
  ejs_available: true,
}

describe('YtDlpSection', () => {
  beforeEach(() => {
    vi.mocked(request).mockReset()
  })

  it('shows installed and latest versions plus distinct Deno and EJS status', async () => {
    vi.mocked(request).mockResolvedValueOnce(config as never)
    const wrapper = mount(YtDlpSection)
    await flushPromises()

    expect(wrapper.text()).toContain('2026.10.21')
    expect(wrapper.text()).toContain('2026.10.23.233055')
    expect(wrapper.text()).toContain('Deno が見つかりません')
    expect(wrapper.text()).toContain('yt-dlp-ejs を確認済み')
    expect(wrapper.text()).toContain('Cookie Profile')
  })

  it('saves a release channel and starts a forced reinstall', async () => {
    vi.mocked(request)
      .mockResolvedValueOnce(config as never)
      .mockResolvedValueOnce({ ...config, channel: 'master', auto_update: false } as never)
      .mockResolvedValueOnce({ started: true, force: true } as never)
      .mockResolvedValueOnce({
        ...config,
        channel: 'master',
        auto_update: false,
        update_in_progress: true,
      } as never)
    const wrapper = mount(YtDlpSection)
    await flushPromises()

    await wrapper.get('select').setValue('master')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('設定を保存'))!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('強制再インストール'))!
      .trigger('click')
    await flushPromises()

    expect(vi.mocked(request).mock.calls[1]?.[0]).toBe('/settings/ytdlp')
    expect(JSON.parse(String(vi.mocked(request).mock.calls[1]?.[1]?.body))).toEqual({
      channel: 'master',
      auto_update: false,
    })
    expect(vi.mocked(request).mock.calls[2]?.[0]).toBe('/settings/ytdlp/update')
    expect(JSON.parse(String(vi.mocked(request).mock.calls[2]?.[1]?.body))).toEqual({ force: true })
    expect(wrapper.text()).toContain('更新処理を開始しました')
    wrapper.unmount()
  })

  it('registers and removes a YouTube cookie file without displaying its contents', async () => {
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path === '/settings/ytdlp') return { ...config, youtube_cookies_configured: false } as never
      if (path === '/settings/youtube-cookies' && init?.method === 'PUT') return { configured: true } as never
      if (path === '/settings/youtube-cookies' && init?.method === 'DELETE')
        return { configured: false } as never
      throw new Error(`unexpected request ${path} ${init?.method}`)
    })
    const wrapper = mount(YtDlpSection)
    await flushPromises()
    const file = new File(['private-cookie'], 'cookies.txt', { type: 'text/plain' })
    const input = wrapper.get<HTMLInputElement>('#youtube-cookie-file')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await wrapper.get('[data-action="save-youtube-cookies"]').trigger('click')
    await flushPromises()
    expect(
      vi
        .mocked(request)
        .mock.calls.some(
          ([path, init]) =>
            path === '/settings/youtube-cookies' && init?.method === 'PUT' && init.body === file,
        ),
    ).toBe(true)
    expect(wrapper.text()).toContain('登録済み')
    expect(wrapper.text()).not.toContain('private-cookie')
    await wrapper.get('[data-action="remove-youtube-cookies"]').trigger('click')
    await wrapper.get('[data-action="confirm-remove-youtube-cookies"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('未登録')
    expect(
      vi
        .mocked(request)
        .mock.calls.some(([path, init]) => path === '/settings/youtube-cookies' && init?.method === 'DELETE'),
    ).toBe(true)
    wrapper.unmount()
  })
})
