import { describe, expect, it } from 'vitest'
import { bytesToGb, formatBytes, formatDate, formatDuration, gbToBytes, relativeTime } from '../format'
describe('format helpers', () => {
  it('formats a local date in 24 hour YYYY/MM/DD HH:mm form', () => {
    expect(formatDate('2026-10-02T13:04:00Z')).toMatch(/^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}$/)
    expect(formatDate(null)).toBe('—')
  })
  it('formats relative timestamps in either direction', () => {
    expect(relativeTime('2026-10-01T00:00:00Z', Date.parse('2026-10-01T00:03:00Z'))).toBe('3 分前')
    expect(relativeTime('2026-10-01T02:05:00Z', Date.parse('2026-10-01T00:00:00Z'))).toBe('あと 2 時間 5 分')
    expect(relativeTime('2030-01-01T00:00:00Z', Date.parse('2026-01-01T00:00:00Z'))).toMatch(/^2030\/01\/01 /)
  })
  it('formats duration and byte boundaries', () => {
    expect(formatDuration(0)).toBe('0:00:00')
    expect(formatDuration(3605)).toBe('1:00:05')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1024)).toBe('1.0 KB')
  })
  it('converts GB and bytes safely', () => {
    expect(gbToBytes(1.25)).toBe(1342177280)
    expect(gbToBytes('')).toBeNull()
    expect(bytesToGb(null)).toBe('')
    expect(bytesToGb(1073741824)).toBe(1)
  })
})
