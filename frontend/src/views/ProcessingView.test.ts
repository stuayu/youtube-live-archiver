import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ProcessingView from './ProcessingView.vue'
import { workspaceKey } from '../composables/workspaceContext'
import type { ProcessingJob, ProcessingJobDetail } from '../api/types'

const { processingJobs, processingJob } = vi.hoisted(() => ({
  processingJobs: vi.fn(),
  processingJob: vi.fn(),
}))

vi.mock('../api/resources', () => ({
  api: { processingJobs, processingJob },
}))

const job: ProcessingJob = {
  id: '17',
  kind: 'SUMMARIZE_CHUNKS',
  status: 'processing',
  attempts: 2,
  error: null,
  next_attempt_at: null,
  created_at: '2026-11-02T00:00:00Z',
  started_at: '2026-11-02T00:01:00Z',
  updated_at: '2026-11-02T00:01:00Z',
  finished_at: null,
  video: {
    id: '3',
    youtube_video_id: 'video1234567',
    title: 'ライブ配信の記録',
    channel_name: '配信チャンネル',
  },
  progress: { done: 1, total: 3 },
  current_chunk: { start: 3600, end: 5400 },
  queue_position: 1,
}

const detail: ProcessingJobDetail = {
  ...job,
  chunks: [
    { index: 0, start: 0, end: 3600, state: 'done', summary: '冒頭の要約' },
    { index: 1, start: 3600, end: 5400, state: 'current', summary: null },
    { index: 2, start: 5400, end: 7200, state: 'pending', summary: null },
  ],
}

const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.clearAllMocks()
})

describe('ProcessingView', () => {
  it('shows Japanese job labels, progress, time range, and expandable chunk summaries', async () => {
    processingJobs.mockResolvedValue({
      items: [job],
      total: 1,
      offset: 0,
      limit: 30,
      counts: { total: 1, by_kind: { SUMMARIZE_CHUNKS: { processing: 1 } }, by_status: { processing: 1 } },
    })
    processingJob.mockResolvedValue(detail)
    const wrapper = mount(ProcessingView, {
      global: {
        provide: {
          [workspaceKey as symbol]: { openVideo: vi.fn(), processingRefresh: ref(0) },
        },
      },
    })
    wrappers.push(wrapper)
    await flushPromises()

    expect(wrapper.text()).toContain('区間要約')
    expect(wrapper.text()).toContain('処理中')
    expect(wrapper.text()).toContain('1/3')
    expect(wrapper.text()).toContain('01:00:00–01:30:00')

    await wrapper.get('[aria-label="区間を表示"]')?.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('冒頭の要約')
    expect(wrapper.text()).toContain('未処理')
  })
})
