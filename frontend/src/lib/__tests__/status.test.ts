import { describe, expect, it } from 'vitest'
import { monitorStatus, protectionAction, protectionReason, videoStatus } from '../status'
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
  it('labels every protection reason the backend can store', () => {
    for (const value of [
      'REMOTE_PRIVATE',
      'REMOTE_DELETED',
      'REMOTE_UNAVAILABLE',
      'REMOTE_CONTENT_CHANGED',
      'REMOTE_DURATION_CHANGED',
      'REMOTE_CHECK_FAILED',
      'LEGAL_HOLD',
      'MANUAL',
    ])
      expect(protectionReason(value)).not.toBe(value)
  })
  it('allows releasing any protection except a legal hold', () => {
    const protectedBy = (reason: string) => ({
      status: 'PROTECTED',
      protected: true,
      protection_reason: reason,
    })
    expect(protectionAction(protectedBy('MANUAL'))).toBe('unprotect')
    expect(protectionAction(protectedBy('REMOTE_DURATION_CHANGED'))).toBe('unprotect')
    expect(protectionAction(protectedBy('REMOTE_PRIVATE'))).toBe('unprotect')
    expect(protectionAction(protectedBy('LEGAL_HOLD'))).toBeNull()
  })
})
