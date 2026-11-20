const icons = {
  back: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/><text x="12" y="16.5" font-size="7" text-anchor="middle" fill="currentColor">10</text></svg>',
  forward:
    '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M12 5V2l5 4-5 4V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z"/><text x="12" y="16.5" font-size="7" text-anchor="middle" fill="currentColor">10</text></svg>',
}

/** 再生位置の上限。ライブ中は duration が Infinity なので、追いかけ再生できる末尾まで。 */
function lastPosition(video: HTMLVideoElement) {
  if (Number.isFinite(video.duration)) return video.duration
  return video.seekable.length ? video.seekable.end(video.seekable.length - 1) : video.currentTime
}

/** DPlayer は矢印キーでしか前後に移動できないため、タッチ操作でも使えるボタンを再生ボタンの隣に置く。 */
export function installSkipButtons(root: HTMLElement, video: HTMLVideoElement, seconds = 10): () => void {
  const play = root.querySelector<HTMLElement>('.dplayer-icons-left .dplayer-play-icon')
  if (!play) return () => {}
  const make = (direction: 'back' | 'forward') => {
    const button = document.createElement('button')
    const label = direction === 'back' ? `${seconds}秒戻る` : `${seconds}秒進む`
    button.type = 'button'
    button.className = `dplayer-icon yla-skip yla-skip-${direction}`
    button.setAttribute('aria-label', label)
    button.title = label
    button.innerHTML = icons[direction]
    button.addEventListener('click', () => {
      const delta = direction === 'back' ? -seconds : seconds
      video.currentTime = Math.min(lastPosition(video), Math.max(0, video.currentTime + delta))
    })
    return button
  }
  const buttons = [make('back'), make('forward')]
  play.after(...buttons)
  return () => buttons.forEach((button) => button.remove())
}
