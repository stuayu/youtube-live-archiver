import { describe, expect, it } from 'vitest'
import { installSkipButtons } from './playerSkip'

function fixture() {
  document.body.innerHTML = `
    <div class="root"><div class="dplayer-icons-left">
      <button class="dplayer-play-icon"></button><div class="dplayer-volume"></div>
    </div></div>`
  const root = document.querySelector<HTMLElement>('.root')!
  const video = document.createElement('video')
  Object.defineProperty(video, 'duration', { value: 100, configurable: true })
  return { root, video }
}

describe('skip buttons', () => {
  it('adds back and forward buttons right after the play button', () => {
    const { root, video } = fixture()
    installSkipButtons(root, video)
    const buttons = [...root.querySelectorAll<HTMLButtonElement>('.dplayer-icons-left > button')]
    expect(buttons.map((button) => button.getAttribute('aria-label'))).toEqual([null, '10秒戻る', '10秒進む'])
  })

  it('moves the playback position and stays within the video', () => {
    const { root, video } = fixture()
    installSkipButtons(root, video)
    const [back, forward] = root.querySelectorAll<HTMLButtonElement>('.yla-skip')
    video.currentTime = 50
    forward!.click()
    expect(video.currentTime).toBe(60)
    back!.click()
    back!.click()
    expect(video.currentTime).toBe(40)
    video.currentTime = 5
    back!.click()
    expect(video.currentTime).toBe(0)
    video.currentTime = 95
    forward!.click()
    expect(video.currentTime).toBe(100)
  })

  it('removes its buttons on dispose', () => {
    const { root, video } = fixture()
    const dispose = installSkipButtons(root, video)
    dispose()
    expect(root.querySelector('.yla-skip')).toBeNull()
  })
})
