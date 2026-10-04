import { describe, expect, it } from 'vitest'
import { groupUpcoming, homeUpcoming, upcomingStartLabel } from '../upcoming'

const now = new Date('2026-10-04T03:00:00+09:00')
const item = (id: string, scheduled_start_at: string | null, status = 'WAITING_LIVE') => ({
  id,
  scheduled_start_at,
  status,
})

describe('upcoming helpers', () => {
  it('groups today, tomorrow, later dates, then unknown times in chronological order', () => {
    const groups = groupUpcoming(
      [
        item('later', '2026-10-07T12:00:00+09:00'),
        item('unknown', null),
        item('tomorrow', '2026-10-05T12:00:00+09:00'),
        item('today', '2026-10-04T08:00:00+09:00'),
      ],
      now,
    )
    expect(groups.map((group) => group.label)).toEqual(['今日', '明日', '10 月 7 日（水）', '時刻未定'])
    expect(groups.map((group) => group.items[0]?.id)).toEqual(['today', 'tomorrow', 'later', 'unknown'])
  })

  it('limits home to the nearest five future, non-recording events', () => {
    const rows = Array.from({ length: 7 }, (_, index) =>
      item(String(index), new Date(now.getTime() + (index - 1) * 60000).toISOString()),
    )
    rows.push(item('recording', new Date(now.getTime() + 1000).toISOString(), 'RECORDING'))
    expect(homeUpcoming(rows, now.getTime()).map((row) => row.id)).toEqual(['2', '3', '4', '5', '6'])
  })

  it('labels overdue and unknown start times explicitly', () => {
    expect(upcomingStartLabel(item('late', '2026-10-04T02:00:00+09:00'), now.getTime())).toBe(
      '開始待ち（予定を 60 分超過）',
    )
    expect(upcomingStartLabel(item('unknown', null), now.getTime())).toBe('時刻未定')
  })
})
