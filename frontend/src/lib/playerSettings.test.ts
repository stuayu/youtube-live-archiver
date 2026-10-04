import { describe, expect, it } from 'vitest'
import { defaultDanmakuPreferences, readDanmakuPreferences, writeDanmakuPreferences } from './playerSettings'
import { chatReplayRange } from './playerPlayback'

describe('danmaku preferences', () => {
  it('uses readable desktop and mobile defaults', () => {
    expect(defaultDanmakuPreferences().font).toBe(20)
    expect(defaultDanmakuPreferences(true).font).toBe(16)
    expect(defaultDanmakuPreferences().quantity).toBe('normal')
  })

  it('persists and restores settings and falls back for invalid stored values', () => {
    const values = new Map<string, string>()
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    }
    const changed = { ...defaultDanmakuPreferences(true), enabled: false, opacity: 0.4, includeDeleted: true }
    writeDanmakuPreferences(storage, changed)
    expect(readDanmakuPreferences(storage, true)).toEqual(changed)
    storage.setItem('yla-danmaku', '{broken')
    expect(readDanmakuPreferences(storage, true)).toEqual(defaultDanmakuPreferences(true))
  })
})

describe('chat replay range', () => {
  it('tracks the previous window and clamps times below zero', () => {
    expect(chatReplayRange(155)).toEqual({ afterMs: 35000, beforeMs: 155000 })
    expect(chatReplayRange(4)).toEqual({ afterMs: 0, beforeMs: 4000 })
    expect(chatReplayRange(Number.NaN)).toEqual({ afterMs: 0, beforeMs: 0 })
  })
})
