import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import VideoRow from './VideoRow.vue'
import type { Video } from '../api/types'
const sample = (status: string): Video => ({
  id: '1',
  youtube_video_id: 'abcdefghijk',
  channel_id: '1',
  title: 'test',
  status,
  remote_status: 'PUBLIC',
  retention_expires_at: null,
  protected: false,
  protection_reason: null,
  protected_at: null,
  is_partial: false,
  recovery_attempts: 0,
  next_recovery_at: null,
  recording_finished_at: null,
  file_size: 1,
  recorded_at: null,
  recording_heartbeat_at: null,
  original_duration: null,
  has_file: true,
  live_playback_available: false,
  has_thumbnail: false,
  thumbnail_url: null,
  scheduled_start_at: null,
  user_stopped: false,
  duration_seconds: null,
  deletion_after: null,
  last_error: null,
  archive_backfill: null,
  recording_start_offset_ms: 0,
  segment_count: 0,
  recovery_exhausted: false,
  skip_reason: null,
  cancel_reason: null,
  missed_live: false,
  schedule_enabled: true,
  live_status: null,
  transcription_status: 'pending',
  transcription_error: null,
  storage_id: null,
  channel_name: 'channel',
  storage_name: null,
  media_extension: 'mp4',
})
describe('VideoRow', () => {
  it('renders Japanese state labels and only offers state-appropriate actions', async () => {
    const labels = [
      '配信待ち',
      '録画待ち',
      '録画中',
      '録画済み',
      '録画失敗',
      '保存先待ち',
      '確認中',
      '削除候補',
      '確認保留',
      '保護済み',
      'スキップ',
      '中止',
      '自動削除済み',
      '手動削除済み',
    ]
    const states = [
      'WAITING_LIVE',
      'DISCOVERED',
      'RECORDING',
      'ACTIVE',
      'RECORDING_FAILED',
      'WAITING_FOR_STORAGE',
      'VERIFYING_REMOTE',
      'DELETE_ELIGIBLE',
      'PROTECTION_PENDING',
      'PROTECTED',
      'SKIPPED',
      'CANCELLED',
      'DELETED_BY_RETENTION',
      'DELETED_MANUALLY',
    ]
    for (const [index, status] of states.entries()) {
      const wrapper = mount(VideoRow, { props: { video: sample(status) } })
      await wrapper.get('summary').trigger('click')
      expect(wrapper.text()).toContain(labels[index])
      if (status === 'RECORDING') {
        expect(wrapper.text()).toContain('停止')
        expect(wrapper.text()).not.toContain('削除')
        expect(wrapper.text()).not.toContain('永久保存')
      } else {
        expect(wrapper.text()).toContain('削除')
        expect(wrapper.text()).toContain('再生')
        // The API only protects these statuses; other rows must not offer an action that returns 409.
        const protectable = ['ACTIVE', 'DELETE_ELIGIBLE', 'PROTECTION_PENDING', 'VERIFYING_REMOTE']
        expect(wrapper.text().includes('永久保存')).toBe(protectable.includes(status))
      }
      wrapper.unmount()
    }
  })

  it('hides deletion from the deleted-history scope', async () => {
    const wrapper = mount(VideoRow, { props: { video: sample('ACTIVE'), deleted: true } })
    await wrapper.get('summary').trigger('click')
    expect(wrapper.text()).not.toContain('削除')
  })

  it('shows where the transcript and summary pipeline is for finished recordings', () => {
    const finished = { ...sample('ACTIVE'), recording_finished_at: '2026-11-01T00:00:00Z' }
    const cases: Array<[Partial<Video>, string]> = [
      [{ transcription_status: 'pending' }, '文字起こし待ち'],
      [{ transcription_status: 'processing' }, '文字起こし中'],
      [{ transcription_status: 'failed', transcription_error: 'CUDA error' }, '文字起こし失敗'],
      [{ transcription_status: 'completed', summary_status: 'pending' }, '要約待ち'],
      [{ transcription_status: 'completed', summary_status: 'processing' }, '要約中'],
      [{ transcription_status: 'completed', summary_status: 'retry_wait' }, '要約再試行待ち'],
      [{ transcription_status: 'completed', summary_status: 'completed' }, '要約済み'],
      [{ transcription_status: 'completed', summary_status: 'disabled' }, '文字起こし済み'],
      [
        {
          transcription_status: 'completed',
          summary_status: 'processing',
          summary_progress: { done: 3, total: 19 },
        },
        '要約中 3/19',
      ],
      [
        {
          transcription_status: 'completed',
          summary_status: 'processing',
          summary_progress: { done: 19, total: 19 },
        },
        '要約中（まとめ）',
      ],
    ]
    for (const [patch, label] of cases) {
      const wrapper = mount(VideoRow, { props: { video: { ...finished, ...patch } } })
      expect(wrapper.text()).toContain(label)
      wrapper.unmount()
    }
    const failed = mount(VideoRow, {
      props: { video: { ...finished, transcription_status: 'failed', transcription_error: 'CUDA error' } },
    })
    expect(failed.get('[title="CUDA error"]').text()).toBe('文字起こし失敗')
  })

  it('does not show processing state before a recording has finished', () => {
    for (const status of ['WAITING_LIVE', 'RECORDING', 'RECORDING_FAILED']) {
      const wrapper = mount(VideoRow, {
        props: { video: { ...sample(status), transcription_status: 'pending' } },
      })
      expect(wrapper.text()).not.toContain('文字起こし')
      wrapper.unmount()
    }
  })

  it('offers unprotect only for manual protection', async () => {
    const manual = mount(VideoRow, {
      props: { video: { ...sample('PROTECTED'), protected: true, protection_reason: 'MANUAL' } },
    })
    await manual.get('summary').trigger('click')
    expect(manual.text()).toContain('保護解除')
    const automatic = mount(VideoRow, {
      props: { video: { ...sample('PROTECTED'), protected: true, protection_reason: 'REMOTE_PRIVATE' } },
    })
    await automatic.get('summary').trigger('click')
    expect(automatic.text()).not.toContain('保護解除')
  })
})
