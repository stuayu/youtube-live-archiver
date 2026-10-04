export function missingOpeningLabel(offsetMs: number, liveFromStart?: string | null): string | null {
  if ((liveFromStart === 'used' && offsetMs <= 5_000) || offsetMs <= 0) return null
  return `冒頭 ${Math.ceil(offsetMs / 60_000)} 分欠け`
}
