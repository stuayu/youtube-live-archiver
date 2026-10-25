import { relativeTime } from './format'

export interface ScheduledVideo {
  scheduled_start_at: string | null
  status: string
}

export interface UpcomingGroup<T> {
  label: string
  items: T[]
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function groupUpcoming<T extends ScheduledVideo>(items: T[], now = new Date()): UpcomingGroup<T>[] {
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  const groups = new Map<string, T[]>()
  const ordered = [...items].sort(
    (a, b) =>
      Date.parse(a.scheduled_start_at || '2999-12-31') - Date.parse(b.scheduled_start_at || '2999-12-31'),
  )
  for (const item of ordered) {
    const start = item.scheduled_start_at ? new Date(item.scheduled_start_at) : null
    const key = start && Number.isFinite(start.getTime()) ? dayKey(start) : 'unknown'
    const label =
      key === 'unknown'
        ? '時刻未定'
        : key === dayKey(now)
          ? '今日'
          : key === dayKey(tomorrow)
            ? '明日'
            : `${start!.getMonth() + 1} 月 ${start!.getDate()} 日（${'日月火水木金土'[start!.getDay()]}）`
    groups.set(label, [...(groups.get(label) ?? []), item])
  }
  return [...groups].map(([label, values]) => ({ label, items: values }))
}

export function homeUpcoming<T extends ScheduledVideo>(items: T[], now = Date.now()): T[] {
  return items
    .filter(
      (item) =>
        item.status !== 'RECORDING' &&
        item.scheduled_start_at !== null &&
        Date.parse(item.scheduled_start_at) > now,
    )
    .sort((a, b) => Date.parse(a.scheduled_start_at!) - Date.parse(b.scheduled_start_at!))
    .slice(0, 5)
}

export function upcomingStartLabel(item: ScheduledVideo, now = Date.now()): string {
  if (!item.scheduled_start_at) return '時刻未定'
  const start = Date.parse(item.scheduled_start_at)
  if (!Number.isFinite(start)) return '時刻未定'
  if (start < now) return `開始待ち（予定を ${Math.floor((now - start) / 60000)} 分超過）`
  return relativeTime(item.scheduled_start_at, now)
}
