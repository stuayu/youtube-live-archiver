import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { request } from '../../api/client'
import PlayerSocialTimeline from './PlayerSocialTimeline.vue'

vi.mock('../../api/client', () => ({
  jsonBody: (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) }),
  request: vi.fn(),
}))

const accountIds = {
  blueskyOne: 'a'.repeat(32),
  blueskyTwo: 'b'.repeat(32),
  misskey: 'c'.repeat(32),
}
const accounts = [
  {
    id: accountIds.blueskyOne,
    provider: 'bluesky',
    configured: true,
    label: 'first.bsky.social',
    identifier: 'first.bsky.social',
    service_url: 'https://bsky.social',
  },
  {
    id: accountIds.blueskyTwo,
    provider: 'bluesky',
    configured: true,
    label: 'second.bsky.social',
    identifier: 'second.bsky.social',
    service_url: 'https://bsky.social',
  },
  {
    id: accountIds.misskey,
    provider: 'misskey',
    configured: true,
    label: '@viewer@misskey.example',
    instance_url: 'https://misskey.example',
  },
]
const settings = {
  accounts,
  bluesky: { configured: true, identifier: 'first.bsky.social', service_url: 'https://bsky.social' },
  misskey: { configured: true, instance_url: 'https://misskey.example' },
}

function mountTimeline() {
  return mount(PlayerSocialTimeline, {
    props: { videoId: 'video-1', youtubeVideoId: 'abcdefghijk', playbackSeconds: 10 },
  })
}

describe('PlayerSocialTimeline account selection', () => {
  beforeEach(() => {
    vi.mocked(request).mockReset()
    vi.mocked(request).mockResolvedValue(settings as never)
  })

  it('selects several logged-in accounts for one post and retries only the failed account', async () => {
    vi.mocked(request)
      .mockResolvedValueOnce(settings as never)
      .mockResolvedValueOnce({
        results: {
          [accountIds.blueskyOne]: {
            provider: 'bluesky',
            label: 'first.bsky.social',
            success: true,
            url: 'https://bsky.app/post/one',
          },
          [accountIds.misskey]: {
            provider: 'misskey',
            label: '@viewer@misskey.example',
            success: false,
            error: '一時的なエラー',
          },
        },
      } as never)
    const wrapper = mountTimeline()
    await flushPromises()

    await wrapper.get('#social-post-tab').trigger('click')
    const targets = wrapper.findAll('.social-post-account input')
    expect(targets).toHaveLength(3)
    expect(targets.map((target) => (target.element as HTMLInputElement).checked)).toEqual([
      false,
      false,
      false,
    ])
    await targets[0]!.setValue(true)
    await targets[2]!.setValue(true)
    await wrapper.get('textarea').setValue('配信の見どころ')
    await wrapper.get('.social-post-form').trigger('submit')
    await flushPromises()

    const postCall = vi.mocked(request).mock.calls.find(([path]) => String(path).endsWith('/social/post'))
    expect(JSON.parse(String((postCall?.[1] as { body: string }).body)).targets).toEqual([
      accountIds.blueskyOne,
      accountIds.misskey,
    ])
    expect((targets[0]!.element as HTMLInputElement).checked).toBe(false)
    expect((targets[2]!.element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.text()).toContain('first.bsky.social: 投稿しました')
    expect(wrapper.text()).toContain('@viewer@misskey.example: 一時的なエラー')
  })

  it('uses the selected Misskey account for hashtag search', async () => {
    vi.mocked(request)
      .mockResolvedValueOnce(settings as never)
      .mockResolvedValueOnce({
        items: [],
        providers: {
          misskey: { configured: true, error: null, cursor: null, account_id: accountIds.misskey },
        },
      } as never)
    const wrapper = mountTimeline()
    await flushPromises()
    const accountSelect = wrapper.get('.social-search-account select')
    expect((accountSelect.element as HTMLSelectElement).value).toBe(accountIds.misskey)
    await wrapper.get('input[placeholder="#配信名"]').setValue('配信')
    await wrapper.get('.social-search-form').trigger('submit')
    await flushPromises()

    const searchCall = vi
      .mocked(request)
      .mock.calls.find(([path]) => String(path).includes('/social/search?'))
    expect(String(searchCall?.[0])).toContain(`misskey_account_id=${accountIds.misskey}`)
  })
})
