import { describe, expect, it } from 'vitest'
import { monitorStatus, protectionReason, videoStatus } from '../status'
describe('status presentation', () => {
  it('labels every backend state in Japanese', () => {
    for (const value of [
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
    ])
      expect(videoStatus(value).label).not.toBe(value)
  })
  it('preserves unknown state names without throwing', () => {
    expect(videoStatus('NEW_STATE').label).toBe('NEW_STATE')
  })
  it('localizes reasons and channel monitor state', () => {
    expect(protectionReason('REMOTE_PRIVATE')).toBe('YouTube 側で非公開')
    expect(monitorStatus('paused').label).toBe('停止中')
  })
})
