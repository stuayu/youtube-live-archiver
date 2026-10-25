import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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
})
