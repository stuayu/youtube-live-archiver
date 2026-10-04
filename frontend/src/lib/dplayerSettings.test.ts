import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DanmakuPreferences } from './playerSettings'
import { installDPlayerSettings } from './dplayerSettings'
import type { DPlayerSettingsAdapter } from './dplayerSettings'

function fixture() {
  document.body.innerHTML = `
    <div class="dplayer-mount">
      <div class="dplayer-video-wrap"><div class="dplayer-danmaku"></div></div>
      <div class="dplayer-controller"><div class="dplayer-icons-left"></div>
        <div class="dplayer-icons-right">
          <div class="dplayer-setting"><button class="dplayer-setting-icon"></button>
            <div class="dplayer-setting-box"><div class="dplayer-setting-origin-panel">
              <div class="dplayer-setting-item dplayer-setting-speed"><span class="dplayer-label">速度</span></div>
              <div class="dplayer-setting-item dplayer-setting-loop"><span class="dplayer-label">ループ</span>
                <div class="dplayer-toggle"><input class="dplayer-toggle-setting-input" type="checkbox" /></div>
              </div>
              <div class="dplayer-setting-item dplayer-setting-showdan"><span class="dplayer-label">弾幕</span>
                <input class="dplayer-showdan-setting-input" type="checkbox" />
              </div>
              <div class="dplayer-setting-item dplayer-setting-danunlimit"></div>
              <div class="dplayer-setting-item dplayer-setting-danmaku">
                <span class="dplayer-label">不透明度</span>
                <div class="dplayer-danmaku-bar-wrap"><div class="dplayer-danmaku-bar">
                  <div class="dplayer-danmaku-bar-inner"></div>
                </div></div>
              </div>
            </div><div class="dplayer-setting-speed-panel"></div></div>
          </div>
        </div>
      </div>
    </div>`
  const root = document.querySelector<HTMLElement>('.dplayer-mount')!
  const video = document.createElement('video')
  const callbacks = new Map<string, (value?: number) => void>()
  const player: DPlayerSettingsAdapter = {
    video,
    danmaku: {
      show: vi.fn(),
      hide: vi.fn(),
      opacity: vi.fn(),
      speed: vi.fn(),
      unlimit: vi.fn(),
      reload: vi.fn(),
    },
    speed: vi.fn(),
    on: (name, callback) => callbacks.set(name, callback),
  }
  return { root, player, callbacks }
}

const defaults: DanmakuPreferences = {
  enabled: true,
  opacity: 0.8,
  speed: 16,
  area: 50,
  font: 20,
  quantity: 'normal',
  danmakuKind: '',
  includeDeleted: false,
  playbackRate: 1,
  loop: false,
  liveFollow: true,
}

describe('DPlayer settings extension', () => {
  beforeEach(() => {
    localStorage.clear()
    document.body.innerHTML = ''
  })

  it('adds native-style items under the built-in menu and persists changes', async () => {
    const { root, player, callbacks } = fixture()
    Object.defineProperty(root, 'clientHeight', { configurable: true, value: 320 })
    let current = { ...defaults }
    const handle = installDPlayerSettings({
      root,
      player,
      initial: defaults,
      live: false,
      onFollowLive: vi.fn(),
      onChange: (value) => {
        current = value
        localStorage.setItem('yla-danmaku', JSON.stringify(value))
      },
    })
    const origin = root.querySelector('.dplayer-setting-origin-panel')!
    const settingsBox = root.querySelector<HTMLElement>('.dplayer-setting-box')!
    expect(settingsBox.style.maxHeight).toBe('260px')
    for (const label of [
      '流れる速さ',
      '文字サイズ',
      '表示領域',
      '弾幕の量',
      'コメント種別',
      '削除済みを表示',
    ]) {
      const item = Array.from(origin.querySelectorAll('.dplayer-setting-item')).find((entry) =>
        entry.textContent?.includes(label),
      )
      expect(item).toBeTruthy()
      expect(item?.classList.contains('dplayer-setting-item')).toBe(true)
    }
    const areaValue = origin.querySelector<HTMLElement>('.yla-setting-area .yla-setting-value')!
    expect(areaValue.classList.contains('dplayer-toggle')).toBe(false)
    expect(areaValue.textContent).toBe('半分')
    expect(origin.querySelector('.dplayer-setting-speed .yla-playback-rate-value')?.textContent).toBe('1.0×')
    expect(origin.querySelector('.dplayer-setting-danunlimit')).toBeNull()
    expect(root.querySelector('.dplayer-setting-box .yla-setting-choice-panel')).toBeTruthy()

    const area = origin.querySelector<HTMLElement>('.yla-setting-area')!
    area.click()
    expect(root.querySelector('.dplayer-setting-box')?.classList.contains('dplayer-setting-box-speed')).toBe(
      true,
    )
    const half = Array.from(
      root.querySelectorAll<HTMLElement>('.yla-setting-choice-panel .dplayer-setting-speed-item'),
    ).find((item) => item.textContent === '3/4')!
    half.click()
    expect(current.area).toBe(75)
    expect(areaValue.textContent).toBe('3/4')
    expect(half.classList.contains('yla-selected')).toBe(true)

    const speed = origin.querySelector<HTMLElement>('.yla-setting-flow-speed .dplayer-danmaku-bar-wrap')!
    vi.spyOn(speed, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 100,
      bottom: 20,
      width: 100,
      height: 20,
      toJSON: () => ({}),
    })
    speed.dispatchEvent(new PointerEvent('pointerdown', { clientX: 50, bubbles: true }))
    expect(current.speed).toBe(18)
    expect(speed.getAttribute('aria-valuenow')).toBe('18')
    expect(origin.querySelector('.yla-setting-flow-speed .yla-setting-current-value')?.textContent).toBe('18')
    expect(root.style.getPropertyValue('--danmaku-speed')).toBe(`${16 / 18}s`)
    callbacks.get('danmaku_opacity')?.(0.4)
    expect(current.opacity).toBe(0.4)
    expect(origin.querySelector('.yla-opacity-value')?.textContent).toBe('40%')
    expect(JSON.parse(localStorage.getItem('yla-danmaku') ?? '{}')).toMatchObject({ area: 75, speed: 18 })

    const font = origin.querySelector<HTMLElement>('.yla-setting-font-size .dplayer-danmaku-bar-wrap')!
    vi.spyOn(font, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 100,
      bottom: 20,
      width: 100,
      height: 20,
      toJSON: () => ({}),
    })
    font.dispatchEvent(new PointerEvent('pointerdown', { clientX: 80, bubbles: true }))
    expect(current.font).toBe(28)
    expect(origin.querySelector('.yla-setting-font-size .yla-setting-current-value')?.textContent).toBe(
      '28px',
    )
    expect(root.style.getPropertyValue('--danmaku-size')).toBe('28px')

    handle.dispose()
    const prior = current.speed
    Object.defineProperty(root, 'clientHeight', { configurable: true, value: 500 })
    window.dispatchEvent(new Event('resize'))
    expect(settingsBox.style.maxHeight).toBe('260px')
    speed.dispatchEvent(new PointerEvent('pointerdown', { clientX: 80, bubbles: true }))
    expect(current.speed).toBe(prior)
  })

  it('restores native options, saves native interactions, and adds live follow inside the controller', async () => {
    const { root, player } = fixture()
    const restored = { ...defaults, enabled: false, loop: true, playbackRate: 1.5 }
    const onChange = vi.fn()
    const onFollowLive = vi.fn()
    installDPlayerSettings({ root, player, initial: restored, live: true, onChange, onFollowLive })
    expect(player.danmaku.hide).toHaveBeenCalledOnce()
    expect(player.video.loop).toBe(true)
    expect(player.speed).toHaveBeenCalledWith(1.5)
    const origin = root.querySelector<HTMLElement>('.dplayer-setting-origin-panel')!
    const loopInput = origin.querySelector<HTMLInputElement>('.dplayer-toggle-setting-input')!
    loopInput.checked = false
    origin.querySelector('.dplayer-setting-loop')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await Promise.resolve()
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ loop: false }))
    expect(root.querySelector('.dplayer-live-badge')?.closest('.dplayer-controller')).toBeTruthy()
    root.querySelector('.dplayer-live-badge')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(onFollowLive).toHaveBeenCalledOnce()
    const follow = origin.querySelector<HTMLElement>('.yla-setting-live-follow')!
    follow.click()
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ liveFollow: false }))
  })
})
