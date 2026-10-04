export interface ChatReplayRange {
  afterMs: number
  beforeMs: number
}

export function chatReplayRange(seconds: number, windowSeconds = 120): ChatReplayRange {
  const end = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  const start = Math.max(0, end - Math.max(0, windowSeconds))
  return { afterMs: Math.floor(start * 1000), beforeMs: Math.floor(end * 1000) }
}
