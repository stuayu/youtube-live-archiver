import type { ActivityProfile } from '../api/types'

export function localActivityHours(profile: ActivityProfile): string[] {
  const result = Array<string>(24).fill('normal')
  const offset = new Date().getTimezoneOffset() / 60
  profile.hours.forEach((level, utcHour) => {
    result[Math.floor(utcHour - offset + 48) % 24] = level
  })
  return result
}

export function localHotRanges(profile: ActivityProfile): string {
  const hours = localActivityHours(profile)
  if (!hours.includes('hot')) return 'なし'
  if (hours.every((level) => level === 'hot')) return '終日'
  const ranges: string[] = []
  for (let start = 0; start < 24; start += 1) {
    if (hours[start] !== 'hot' || hours[(start + 23) % 24] === 'hot') continue
    let end = start
    while (hours[(end + 1) % 24] === 'hot' && (end + 1) % 24 !== start) end = (end + 1) % 24
    ranges.push(`${String(start).padStart(2, '0')}:00–${String((end + 1) % 24).padStart(2, '0')}:00`)
  }
  return ranges.join(', ')
}

export function activitySummary(profile: ActivityProfile): string {
  if (profile.samples < 8)
    return '配信の履歴が少ないため、30〜60 分の間隔で確認します（8 本以上で学習します）'
  return `直近 90 日の配信 ${profile.samples} 本から学習。配信が多い時間帯: ${localHotRanges(profile)}。この時間帯は 15〜30 分、それ以外は 30 分〜4 時間の間隔で確認します`
}
