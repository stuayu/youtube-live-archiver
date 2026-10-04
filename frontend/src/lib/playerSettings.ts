export interface DanmakuPreferences {
  enabled: boolean
  opacity: number
  speed: number
  area: number
  font: number
  quantity: string
  danmakuKind: string
  includeDeleted: boolean
  playbackRate: number
  loop: boolean
  liveFollow: boolean
}

export function defaultDanmakuPreferences(mobile = false): DanmakuPreferences {
  return {
    enabled: true,
    opacity: 0.8,
    speed: 16,
    area: 50,
    font: mobile ? 16 : 20,
    quantity: 'normal',
    danmakuKind: '',
    includeDeleted: false,
    playbackRate: 1,
    loop: false,
    liveFollow: true,
  }
}

export function readDanmakuPreferences(
  storage: Pick<Storage, 'getItem'>,
  mobile = false,
): DanmakuPreferences {
  try {
    const raw = storage.getItem('yla-danmaku')
    if (raw)
      return { ...defaultDanmakuPreferences(mobile), ...(JSON.parse(raw) as Partial<DanmakuPreferences>) }
  } catch {
    // Invalid or unavailable local storage should use the defaults.
  }
  return defaultDanmakuPreferences(mobile)
}

export function writeDanmakuPreferences(storage: Pick<Storage, 'setItem'>, preferences: DanmakuPreferences) {
  storage.setItem('yla-danmaku', JSON.stringify(preferences))
}
