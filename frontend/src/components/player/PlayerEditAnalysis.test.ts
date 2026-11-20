import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { EditAnalysis } from '../../api/types'
import PlayerEditAnalysis from './PlayerEditAnalysis.vue'

const completed: EditAnalysis = {
  status: 'completed',
  error: null,
  updated_at: '2026-11-19T00:00:00Z',
  result: {
    local_duration: 8032,
    remote_duration: 6432,
    matched_ratio: 0.99,
    removed: [
      { start: 0, end: 600, remote_at: 0 },
      { start: 3600, end: 4600, remote_at: 3000 },
    ],
    added: [{ start: 5000, end: 5060, local_at: 6600 }],
    segments: [],
  },
}

function render(analysis: EditAnalysis, protectionReason: string | null = 'REMOTE_DURATION_CHANGED') {
  return mount(PlayerEditAnalysis, {
    props: { analysis, youtubeVideoId: 'abcdefghijk', protectionReason, busy: false },
  })
}

describe('PlayerEditAnalysis', () => {
  it('lists cut sections and seeks to them in the recording', async () => {
    const wrapper = render(completed)
    expect(wrapper.text()).toContain('YouTube 版でカットされた区間 2 件')
    expect(wrapper.text()).toContain('1:00:00〜1:16:40')
    expect(wrapper.text()).toContain('16分40秒')
    await wrapper.findAll('button.edit-region-seek')[1]!.trigger('click')
    expect(wrapper.emitted('seek')).toEqual([[3600]])
    const link = wrapper.findAll<HTMLAnchorElement>('a.edit-region-youtube')[1]!
    expect(link.attributes('href')).toBe('https://www.youtube.com/watch?v=abcdefghijk&t=3000s')
  })

  it('lists sections only on YouTube with their recording position', async () => {
    const wrapper = render(completed)
    expect(wrapper.text()).toContain('YouTube 版にだけある区間 1 件')
    const added = wrapper.find('.edit-region-added')
    expect(added.text()).toContain('1:23:20〜1:24:20')
    await added.find('button.edit-region-seek').trigger('click')
    expect(wrapper.emitted('seek')).toEqual([[6600]])
  })

  it('explains an unedited result', () => {
    const wrapper = render({
      ...completed,
      result: { ...completed.result!, removed: [], added: [] },
    })
    expect(wrapper.text()).toContain('YouTube 版との違いは見つかりませんでした')
  })

  it('warns when too little matched to trust the result', () => {
    const wrapper = render({ ...completed, result: { ...completed.result!, matched_ratio: 0.3 } })
    expect(wrapper.text()).toContain('参考程度')
  })

  it('offers to start an analysis and shows progress and errors', async () => {
    const idle = render({ status: 'none', error: null, result: null, updated_at: null })
    await idle.find('button.edit-analysis-run').trigger('click')
    expect(idle.emitted('run')).toHaveLength(1)
    expect(render({ status: 'processing', error: null, result: null, updated_at: null }).text()).toContain(
      '解析中',
    )
    const failed = render({ status: 'retry_wait', error: 'HTTP Error 403', result: null, updated_at: null })
    expect(failed.text()).toContain('HTTP Error 403')
  })
})
