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
        expect(wrapper.text()).toContain('永久保存')
        expect(wrapper.text()).toContain('再生')
      }
      wrapper.unmount()
    }
  })

  it('hides deletion from the deleted-history scope', async () => {
    const wrapper = mount(VideoRow, { props: { video: sample('ACTIVE'), deleted: true } })
    await wrapper.get('summary').trigger('click')
    expect(wrapper.text()).not.toContain('削除')
  })
})
