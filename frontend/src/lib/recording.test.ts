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
})
