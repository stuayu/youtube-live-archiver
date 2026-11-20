import type { EditAnalysisResult } from '../api/types'
import { formatDuration } from './format'

export interface EditMarker {
  kind: 'removed' | 'added'
  start: number
  end: number
  /** シークバー上の位置 (%) */
  left: number
  width: number
  label: string
}

function percent(seconds: number, duration: number) {
  return Math.round(Math.min(100, Math.max(0, (seconds / duration) * 100)) * 100) / 100
}

/** 録画の時刻に置き換えた編集箇所。YouTube 版だけの区間は録画上の挿入位置に点で示す。 */
export function editMarkers(result: EditAnalysisResult | null | undefined, duration: number): EditMarker[] {
  if (!result || !Number.isFinite(duration) || duration <= 0) return []
  const removed = result.removed.map((item) => ({
    kind: 'removed' as const,
    start: item.start,
    end: item.end,
    left: percent(item.start, duration),
    width: Math.round((percent(item.end, duration) - percent(item.start, duration)) * 100) / 100,
    label: `${formatDuration(item.start)}〜${formatDuration(item.end)} は YouTube 版でカット`,
  }))
  const added = result.added
    .filter((item): item is typeof item & { local_at: number } => item.local_at !== null)
    .map((item) => ({
      kind: 'added' as const,
      start: item.local_at,
      end: item.local_at,
      left: percent(item.local_at, duration),
      width: 0,
      label: `${formatDuration(item.local_at)} の位置に YouTube 版だけの映像 ${formatDuration(item.end - item.start)}`,
    }))
  return [...removed, ...added].sort((a, b) => a.start - b.start)
}

/** DPlayer のシークバーに重ねて描く。DPlayer は作り直されるので、呼ぶたびに描き直す。 */
export function renderEditMarkers(root: HTMLElement, markers: EditMarker[]): () => void {
  const dispose = () => root.querySelector('.yla-edit-marker-layer')?.remove()
  dispose()
  const bar = root.querySelector<HTMLElement>('.dplayer-bar-wrap')
  if (!bar || !markers.length) return dispose
  const layer = document.createElement('div')
  layer.className = 'yla-edit-marker-layer'
  layer.setAttribute('aria-hidden', 'true')
  for (const marker of markers) {
    const item = document.createElement('div')
    item.className = `yla-edit-marker yla-edit-marker-${marker.kind}`
    item.style.left = `${marker.left}%`
    item.style.width = `${marker.width}%`
    item.title = marker.label
    layer.append(item)
  }
  bar.append(layer)
  return dispose
}

export function youtubeAt(videoId: string, seconds: number) {
  return `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}&t=${Math.max(0, Math.floor(seconds))}s`
}
