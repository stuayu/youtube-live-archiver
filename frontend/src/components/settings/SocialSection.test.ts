import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { request } from '../../api/client'
import SocialSection from './SocialSection.vue'

vi.mock('../../api/client', () => ({ request: vi.fn() }))

const accounts = [
  {
    id: 'a'.repeat(32),
    provider: 'bluesky',
    configured: true,
    label: 'first.bsky.social',
    identifier: 'first.bsky.social',
    service_url: 'https://bsky.social',
  },
  {
    id: 'b'.repeat(32),
    provider: 'bluesky',
    configured: true,
    label: 'second.bsky.social',
    identifier: 'second.bsky.social',
    service_url: 'https://bsky.social',
  },
  {
    id: 'c'.repeat(32),
    provider: 'misskey',
    configured: true,
    label: '@viewer@misskey.example',
    instance_url: 'https://misskey.example',
  },
]
const settings = {
  accounts,
  bluesky: {
    configured: true,
    identifier: 'private-login@example.net',
    service_url: 'https://bsky.social',
  },
  misskey: { configured: true, instance_url: 'https://misskey.example' },
}

describe('SocialSection account management', () => {
  beforeEach(() => vi.mocked(request).mockReset())
  afterEach(() => vi.restoreAllMocks())

  it('lists multiple accounts and disconnects only the selected account', async () => {
    vi.mocked(request)
      .mockResolvedValueOnce(settings as never)
      .mockResolvedValueOnce({
        ...settings,
        accounts: accounts.slice(1),
      } as never)
    const wrapper = mount(SocialSection)
    await flushPromises()

    expect((wrapper.get('input[autocomplete="username"]').element as HTMLInputElement).value).toBe('')

    const blueskyRows = wrapper.findAll('.social-connected-accounts')[0]!.findAll('li')
    expect(blueskyRows.map((row) => row.text())).toHaveLength(2)
    expect(blueskyRows[0]!.text()).toContain('first.bsky.social')
    await blueskyRows[0]!.get('button').trigger('click')
    await flushPromises()

    expect(vi.mocked(request).mock.calls[1]?.[0]).toBe(`/settings/social/accounts/${accounts[0]!.id}`)
    expect(wrapper.findAll('.social-connected-accounts')[0]!.text()).toContain('second.bsky.social')
    expect(wrapper.findAll('.social-connected-accounts')[0]!.text()).not.toContain('first.bsky.social')
  })

  it('connects Misskey through MiAuth without asking for a token', async () => {
    const popup = {
      closed: false,
      opener: window,
      location: { href: '' },
      close: vi.fn(),
    }
    vi.spyOn(window, 'open').mockReturnValue(popup as never)
    vi.mocked(request)
      .mockResolvedValueOnce(settings as never)
      .mockResolvedValueOnce({
        flow_id: 'd'.repeat(32),
        authorization_url: 'https://misskey.example/miauth/session-id?permission=write%3Anotes',
        expires_in: 600,
      } as never)
      .mockResolvedValueOnce({ state: 'connected', settings } as never)

    const wrapper = mount(SocialSection)
    await flushPromises()
    const misskeySection = wrapper.findAll('.social-setting-provider')[1]!
    expect(misskeySection.find('input[type="password"]').exists()).toBe(false)
    expect(misskeySection.text()).not.toContain('アクセストークン')

    const connectButton = wrapper.findAll('button').find((button) => button.text().includes('Misskeyと連携'))
    await connectButton!.trigger('click')
    await flushPromises()

    expect(vi.mocked(request).mock.calls[1]?.[0]).toBe('/settings/social/misskey/miauth')
    expect(JSON.parse(String(vi.mocked(request).mock.calls[1]?.[1]?.body))).toEqual({
      instance_url: 'https://misskey.example',
    })
    expect(popup.opener).toBeNull()
    expect(popup.location.href).toBe('https://misskey.example/miauth/session-id?permission=write%3Anotes')
    expect(vi.mocked(request).mock.calls[2]?.[0]).toBe(
      `/settings/social/misskey/miauth/${'d'.repeat(32)}/check`,
    )
    expect(wrapper.text()).toContain('Misskey アカウントを追加しました')
  })

  it('lets the user cancel a pending Misskey MiAuth flow', async () => {
    const popup = { closed: false, opener: window, location: { href: '' }, close: vi.fn() }
    vi.spyOn(window, 'open').mockReturnValue(popup as never)
    vi.mocked(request)
      .mockResolvedValueOnce(settings as never)
      .mockResolvedValueOnce({
        flow_id: 'e'.repeat(32),
        authorization_url: 'https://misskey.example/miauth/session-id',
        expires_in: 600,
      } as never)
      .mockResolvedValueOnce({ state: 'pending' } as never)
      .mockResolvedValueOnce({ cancelled: true } as never)

    const wrapper = mount(SocialSection)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Misskeyと連携'))!
      .trigger('click')
    await flushPromises()

    const cancelButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('連携をキャンセル'))
    expect(cancelButton).toBeTruthy()
    await cancelButton!.trigger('click')
    await flushPromises()

    expect(vi.mocked(request).mock.calls[3]?.[0]).toBe(`/settings/social/misskey/miauth/${'e'.repeat(32)}`)
    expect(vi.mocked(request).mock.calls[3]?.[1]?.method).toBe('DELETE')
    expect(popup.close).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('Misskey 連携をキャンセルしました')
  })
})
