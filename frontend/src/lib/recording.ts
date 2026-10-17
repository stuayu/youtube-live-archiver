export function missingOpeningLabel(offsetMs: number, liveFromStart?: string | null): string | null {
  if (liveFromStart === 'used' && offsetMs <= 5_000) return null
  if (liveFromStart === 'skipped_too_old') return '途中から録画'
  if (offsetMs <= 0) return null
  const minutes = Math.ceil(offsetMs / 60_000)
  if (minutes >= 525_600) return `冒頭 約 ${Math.ceil(minutes / 525_600)} 年欠け`
  if (minutes >= 2_880) return `冒頭 約 ${Math.ceil(minutes / 1_440)} 日欠け`
  if (minutes >= 60) return `冒頭 約 ${Math.ceil(minutes / 60)} 時間欠け`
  return `冒頭 ${minutes} 分欠け`
}
