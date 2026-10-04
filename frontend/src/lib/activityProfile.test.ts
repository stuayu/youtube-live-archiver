import { describe, expect, it, vi } from 'vitest'
import type { ActivityProfile } from '../api/types'
import { activitySummary, localActivityHours, localHotRanges } from './activityProfile'

const profile: ActivityProfile = {
  samples: 42,
  mode: 'weekhour',
  hours: Array.from({ length: 24 }, (_, hour) => (hour >= 20 || hour < 2 ? 'hot' : 'cold')),
  levels: Array(168).fill('cold'),
  current_level: 'cold',
  next_check_at: null,
  normal_interval_seconds: [7200, 14400],
  hot_ranges: ['20:00–02:00'],
}

describe('activity profile formatting', () => {
  it('rotates UTC hours into viewer local time', () => {
    vi.stubGlobal(
      'Date',
      class extends Date {
        getTimezoneOffset() {
          return -540
        }
      },
    )
    expect(localActivityHours(profile)[5]).toBe('hot')
    vi.unstubAllGlobals()
  })

  it('formats wrapped local ranges and learned summary', () => {
    expect(localHotRanges(profile)).toMatch(/–/)
    expect(activitySummary(profile)).toContain('直近 90 日の配信 42 本から学習')
    expect(activitySummary(profile)).toContain('15〜30 分')
  })

  it('explains insufficient history', () => {
    expect(activitySummary({ ...profile, samples: 3 })).toContain('8 本以上で学習')
  })
})
