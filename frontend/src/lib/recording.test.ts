import { describe, expect, it } from 'vitest'
import { missingOpeningLabel } from './recording'

describe('missingOpeningLabel', () => {
  it('rounds any positive gap up to whole minutes and hides complete recordings', () => {
    expect(missingOpeningLabel(1, null)).toBe('冒頭 1 分欠け')
    expect(missingOpeningLabel(60_000, 'fallback')).toBe('冒頭 1 分欠け')
    expect(missingOpeningLabel(60_001, null)).toBe('冒頭 2 分欠け')
    expect(missingOpeningLabel(5_000, 'used')).toBeNull()
    expect(missingOpeningLabel(5_001, 'used')).toBe('冒頭 1 分欠け')
    expect(missingOpeningLabel(0, null)).toBeNull()
  })

  it('raises large gaps to compact time units', () => {
    expect(missingOpeningLabel(60 * 60_000, null)).toBe('冒頭 約 1 時間欠け')
    expect(missingOpeningLabel(48 * 60 * 60_000, null)).toBe('冒頭 約 2 日欠け')
    expect(missingOpeningLabel(365 * 24 * 60 * 60_000, null)).toBe('冒頭 約 1 年欠け')
    expect(missingOpeningLabel(365 * 24 * 60 * 60_000, 'skipped_too_old')).toBe('途中から録画')
    expect(missingOpeningLabel(0, 'skipped_too_old')).toBe('途中から録画')
  })
})
