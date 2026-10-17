export interface ChatReplayRange {
  afterMs: number
  beforeMs: number
}

export function chatReplayRange(seconds: number, windowSeconds = 120): ChatReplayRange {
  const end = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  const start = Math.max(0, end - Math.max(0, windowSeconds))
  return { afterMs: Math.floor(start * 1000), beforeMs: Math.floor(end * 1000) }
}

export function danmakuRange(seconds: number, windowSeconds = 180): ChatReplayRange {
  const current = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  const width = Math.max(1, windowSeconds)
  const start = Math.max(0, Math.floor(current / width) * width - 15)
  return { afterMs: Math.floor(start * 1000), beforeMs: Math.floor((start + width) * 1000) }
}
