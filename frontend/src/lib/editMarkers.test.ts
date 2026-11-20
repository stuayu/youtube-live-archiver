import { describe, expect, it } from 'vitest'
import { editMarkers, renderEditMarkers, youtubeAt } from './editMarkers'
import type { EditAnalysisResult } from '../api/types'

const result: EditAnalysisResult = {
  local_duration: 1000,
  remote_duration: 850,
  matched_ratio: 0.98,
  removed: [
    { start: 0, end: 100, remote_at: 0 },
    { start: 500, end: 550, remote_at: 400 },
  ],
  added: [{ start: 700, end: 760, local_at: 800 }],
  segments: [],
}

describe('edit markers', () => {
  it('places removed ranges and insertion points on the recording timeline', () => {
    expect(editMarkers(result, 1000)).toEqual([
      {
        kind: 'removed',
        start: 0,
        end: 100,
        left: 0,
        width: 10,
        label: '0:00:00〜0:01:40 は YouTube 版でカット',
      },
      {
        kind: 'removed',
        start: 500,
        end: 550,
        left: 50,
        width: 5,
        label: '0:08:20〜0:09:10 は YouTube 版でカット',
      },
      {
        kind: 'added',
        start: 800,
        end: 800,
        left: 80,
        width: 0,
        label: '0:13:20 の位置に YouTube 版だけの映像 0:01:00',
      },
    ])
  })

  it('skips insertion points that have no matching recording position', () => {
    const markers = editMarkers(
      { ...result, removed: [], added: [{ start: 0, end: 30, local_at: null }] },
      1000,
    )
    expect(markers).toEqual([])
  })

  it('is empty without a usable duration', () => {
    expect(editMarkers(result, 0)).toEqual([])
    expect(editMarkers(null, 1000)).toEqual([])
  })

  it('draws a marker layer inside the DPlayer bar and removes it on dispose', () => {
    document.body.innerHTML =
      '<div class="root"><div class="dplayer-bar-wrap"><div class="dplayer-bar"></div></div></div>'
    const root = document.querySelector<HTMLElement>('.root')!
    const dispose = renderEditMarkers(root, editMarkers(result, 1000))
    const items = root.querySelectorAll<HTMLElement>('.dplayer-bar-wrap .yla-edit-marker')
    expect(items).toHaveLength(3)
    expect(items[1]!.style.left).toBe('50%')
    expect(items[1]!.style.width).toBe('5%')
    expect(items[1]!.title).toContain('カット')
    expect(items[2]!.classList.contains('yla-edit-marker-added')).toBe(true)
    // 再描画しても重複しない。
    renderEditMarkers(root, editMarkers(result, 1000))
    expect(root.querySelectorAll('.yla-edit-marker-layer')).toHaveLength(1)
    dispose()
    expect(root.querySelector('.yla-edit-marker-layer')).toBeNull()
  })

  it('links to the same moment on YouTube', () => {
    expect(youtubeAt('abc123', 3723.9)).toBe('https://www.youtube.com/watch?v=abc123&t=3723s')
  })
})
