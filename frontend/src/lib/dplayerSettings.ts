import type { DanmakuPreferences } from './playerSettings'

interface DanmakuApi {
  show(): void
  hide(): void
  opacity(value?: number): number | void
  speed(value: number): void
  unlimit(value: boolean): void
  reload(options: {
    id: string
    address: string
    token: string
    maximum: number
    addition: unknown[]
    user: string
    bottom: number
    unlimited: boolean
    mutex: boolean
    speedRate: number
  }): void
}

export interface DPlayerSettingsAdapter {
  video: HTMLVideoElement
  danmaku: DanmakuApi
  speed(value: number): void
  on(name: string, callback: (value?: number) => void): void
}

export interface DPlayerSettingsOptions {
  root: HTMLElement
  player: DPlayerSettingsAdapter
  initial: DanmakuPreferences
  live: boolean
  onChange: (value: DanmakuPreferences) => void
  onFollowLive: () => void
}

export interface DPlayerSettingsHandle {
  setDanmakuEnabled(enabled: boolean): void
  setLiveDelay(seconds: number | null): void
  dispose(): void
}

type ChoiceKey = 'area' | 'quantity' | 'danmakuKind'

const choiceOptions: Record<ChoiceKey, Array<{ value: string; label: string }>> = {
  area: [
    { value: '25', label: '上 1/4' },
    { value: '50', label: '半分' },
    { value: '75', label: '3/4' },
    { value: '100', label: '全体' },
  ],
  quantity: [
    { value: 'few', label: '少なめ' },
    { value: 'normal', label: '標準' },
    { value: 'many', label: '多め' },
    { value: 'unlimited', label: '無制限' },
  ],
  danmakuKind: [
    { value: '', label: 'すべて' },
    { value: 'superchat', label: 'スーパーチャット' },
    { value: 'membership', label: 'メンバーシップ' },
  ],
}

const choiceLabels: Record<ChoiceKey, string> = {
  area: '表示領域',
  quantity: '弾幕の量',
  danmakuKind: 'コメント種別',
}

function makeSettingItem(label: string, className: string): HTMLDivElement {
  const item = document.createElement('div')
  item.className = `dplayer-setting-item ${className}`
  const text = document.createElement('span')
  text.className = 'dplayer-label'
  text.textContent = label
  item.append(text)
  return item
}

function makeRangeItem(label: string, className: string, value: number): HTMLDivElement {
  const item = makeSettingItem(label, className)
  item.classList.add('yla-setting-range-item')
  const current = document.createElement('span')
  current.className = 'yla-setting-current-value'
  current.setAttribute('aria-live', 'polite')
  current.textContent = `${value}`
  item.append(current)
  const wrap = document.createElement('div')
  wrap.className = 'dplayer-danmaku-bar-wrap'
  wrap.setAttribute('role', 'slider')
  wrap.setAttribute('aria-label', label)
  wrap.tabIndex = 0
  const bar = document.createElement('div')
  bar.className = 'dplayer-danmaku-bar'
  const inner = document.createElement('div')
  inner.className = 'dplayer-danmaku-bar-inner'
  inner.style.width = `${value}%`
  const thumb = document.createElement('span')
  thumb.className = 'dplayer-thumb'
  inner.append(thumb)
  bar.append(inner)
  wrap.append(bar)
  wrap.setAttribute('aria-valuenow', `${value}`)
  item.append(wrap)
  return item
}

function makeChoiceItem(key: ChoiceKey, value: string): HTMLDivElement {
  const item = makeSettingItem(choiceLabels[key], `yla-setting-choice yla-setting-${key}`)
  item.setAttribute('role', 'button')
  item.tabIndex = 0
  const current = document.createElement('span')
  current.className = 'yla-setting-current-value yla-setting-value'
  current.textContent = choiceOptions[key].find((option) => option.value === value)?.label ?? ''
  item.append(current)
  return item
}

function makeSwitchItem(label: string, className: string, checked: boolean): HTMLDivElement {
  const item = makeSettingItem(label, className)
  const toggle = document.createElement('div')
  toggle.className = 'dplayer-toggle'
  const input = document.createElement('input')
  input.className = 'yla-toggle-input'
  input.type = 'checkbox'
  input.checked = checked
  input.setAttribute('aria-label', label)
  const switchLabel = document.createElement('label')
  switchLabel.setAttribute('aria-hidden', 'true')
  toggle.append(input, switchLabel)
  item.append(toggle)
  return item
}

export function installDPlayerSettings(options: DPlayerSettingsOptions): DPlayerSettingsHandle {
  const { root, player } = options
  const origin = root.querySelector<HTMLElement>('.dplayer-setting-origin-panel')
  const speedPanel = root.querySelector<HTMLElement>('.dplayer-setting-box')
  const bag: Array<() => void> = []
  if (!origin || !speedPanel)
    return { setDanmakuEnabled: () => {}, setLiveDelay: () => {}, dispose: () => {} }

  const preferences = { ...options.initial }
  root.querySelector<HTMLButtonElement>('.dplayer-setting-icon')?.setAttribute('aria-label', '設定')
  root.querySelectorAll<HTMLButtonElement>('.dplayer-full-icon, .dplayer-full-in-icon').forEach((button) => {
    const label = button.classList.contains('dplayer-full-in-icon') ? 'ウェブ全画面' : '全画面'
    button.setAttribute('aria-label', label)
    button.setAttribute('title', label)
  })
  const listen = (target: EventTarget, name: string, callback: EventListener) => {
    target.addEventListener(name, callback)
    bag.push(() => target.removeEventListener(name, callback))
  }
  const fitSettingsPanel = () => {
    speedPanel.style.maxHeight = `${Math.max(100, root.clientHeight - 60)}px`
  }
  fitSettingsPanel()
  listen(window, 'resize', fitSettingsPanel)
  const save = () => options.onChange({ ...preferences })
  const choiceItems: Partial<Record<ChoiceKey, HTMLElement>> = {}
  const closeSubpanel = () => {
    speedPanel.classList.remove('dplayer-setting-box-narrow', 'dplayer-setting-box-speed')
    speedPanel
      .querySelectorAll<HTMLElement>('.yla-setting-choice-panel')
      .forEach((panel) => (panel.style.display = 'none'))
  }
  const showSubpanel = (panel: HTMLElement) => {
    speedPanel.classList.add('dplayer-setting-box-narrow', 'dplayer-setting-box-speed')
    speedPanel.querySelectorAll<HTMLElement>('.dplayer-setting-speed-panel').forEach((element) => {
      element.style.display = element === panel ? 'block' : 'none'
    })
  }
  const addChoicePanel = (key: ChoiceKey) => {
    const item = makeChoiceItem(key, String(preferences[key]))
    const speedArrow = root
      .querySelector<HTMLElement>('.dplayer-setting-speed .dplayer-toggle')
      ?.cloneNode(true)
    if (speedArrow instanceof HTMLElement) {
      speedArrow.classList.add('yla-setting-arrow')
      item.append(speedArrow)
    }
    choiceItems[key] = item
    const panel = document.createElement('div')
    panel.className = 'dplayer-setting-speed-panel yla-setting-choice-panel'
    panel.style.display = 'none'
    for (const option of choiceOptions[key]) {
      const row = document.createElement('div')
      row.className = 'dplayer-setting-speed-item'
      row.dataset.value = option.value
      row.textContent = option.label
      row.setAttribute('role', 'option')
      row.setAttribute('aria-selected', String(option.value === String(preferences[key])))
      row.classList.toggle('yla-selected', option.value === String(preferences[key]))
      listen(row, 'click', () => {
        if (key === 'area') preferences.area = Number(option.value)
        else if (key === 'quantity') preferences.quantity = option.value
        else preferences.danmakuKind = option.value
        item.querySelector<HTMLElement>('.yla-setting-value')!.textContent = option.label
        item.dataset.currentValue = option.value
        panel.querySelectorAll<HTMLElement>('.dplayer-setting-speed-item').forEach((candidate) => {
          const selected = candidate === row
          candidate.classList.toggle('yla-selected', selected)
          candidate.setAttribute('aria-selected', String(selected))
        })
        if (key === 'area') root.style.setProperty('--danmaku-area', `${preferences.area}%`)
        if (key === 'quantity') player.danmaku.unlimit(option.value === 'unlimited')
        closeSubpanel()
        save()
      })
      panel.append(row)
    }
    listen(item, 'click', () => showSubpanel(panel))
    listen(item, 'keydown', (event) => {
      if (event instanceof KeyboardEvent && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault()
        showSubpanel(panel)
      }
    })
    origin.append(item)
    speedPanel.append(panel)
  }

  const legacyUnlimited = root.querySelector<HTMLElement>('.dplayer-setting-danunlimit')
  legacyUnlimited?.remove()
  addChoicePanel('area')
  addChoicePanel('quantity')
  addChoicePanel('danmakuKind')

  const speedItem = makeRangeItem('流れる速さ', 'yla-setting-flow-speed', preferences.speed)
  const sizeItem = makeRangeItem('文字サイズ', 'yla-setting-font-size', preferences.font)
  origin.append(speedItem, sizeItem, choiceItems.area!, choiceItems.quantity!, choiceItems.danmakuKind!)
  const range = (
    item: HTMLElement,
    min: number,
    max: number,
    initial: number,
    format: (value: number) => string,
    apply: (value: number) => void,
  ) => {
    const wrap = item.querySelector<HTMLElement>('.dplayer-danmaku-bar-wrap')!
    const inner = item.querySelector<HTMLElement>('.dplayer-danmaku-bar-inner')!
    const currentLabel = item.querySelector<HTMLElement>('.yla-setting-current-value')!
    let current = Math.max(min, Math.min(max, initial))
    wrap.setAttribute('role', 'slider')
    wrap.setAttribute(
      'aria-label',
      item.querySelector<HTMLElement>('.dplayer-label')?.textContent?.trim() ?? '',
    )
    wrap.tabIndex = 0
    const updateValue = (value: number) => {
      current = value
      inner.style.width = `${((value - min) / (max - min)) * 100}%`
      wrap.setAttribute('aria-valuemin', String(min))
      wrap.setAttribute('aria-valuemax', String(max))
      wrap.setAttribute('aria-valuenow', String(value))
      currentLabel.textContent = format(value)
      apply(value)
    }
    updateValue(current)
    const update = (event: Event) => {
      const pointer = event as PointerEvent
      const rect = wrap.getBoundingClientRect()
      if (!rect.width) return
      const value = Math.round(
        min + Math.max(0, Math.min(1, (pointer.clientX - rect.left) / rect.width)) * (max - min),
      )
      updateValue(value)
    }
    let pointerId: number | null = null
    listen(wrap, 'pointerdown', (event) => {
      const pointer = event as PointerEvent
      if (pointer.button !== 0) return
      event.preventDefault()
      event.stopPropagation()
      pointerId = pointer.pointerId
      if (wrap.setPointerCapture) wrap.setPointerCapture(pointer.pointerId)
      update(event)
    })
    listen(wrap, 'pointermove', (event) => {
      if ((event as PointerEvent).pointerId === pointerId) update(event)
    })
    const release = (event: Event) => {
      const pointer = event as PointerEvent
      if (pointer.pointerId !== pointerId) return
      pointerId = null
      if (wrap.hasPointerCapture?.(pointer.pointerId)) wrap.releasePointerCapture(pointer.pointerId)
    }
    listen(wrap, 'pointerup', release)
    listen(wrap, 'pointercancel', release)
    listen(wrap, 'click', (event) => event.stopPropagation())
    listen(wrap, 'keydown', (event) => {
      const key = (event as KeyboardEvent).key
      if (key === 'ArrowLeft' || key === 'ArrowRight') {
        event.preventDefault()
        const step = key === 'ArrowRight' ? 1 : -1
        updateValue(Math.max(min, Math.min(max, current + step)))
      } else if (key === 'Home' || key === 'End') {
        event.preventDefault()
        updateValue(key === 'Home' ? min : max)
      }
    })
  }
  range(
    speedItem,
    5,
    30,
    preferences.speed,
    (value) => `${value}`,
    (value) => {
      preferences.speed = value
      player.danmaku.speed(16 / value)
      root.style.setProperty('--danmaku-speed', `${16 / value}s`)
      save()
    },
  )
  range(
    sizeItem,
    12,
    32,
    preferences.font,
    (value) => `${value}px`,
    (value) => {
      preferences.font = value
      root.style.setProperty('--danmaku-size', `${value}px`)
      save()
    },
  )

  const showDanmaku = root.querySelector<HTMLElement>('.dplayer-setting-showdan')
  const showInput = root.querySelector<HTMLInputElement>('.dplayer-showdan-setting-input')
  if (showInput) showInput.checked = preferences.enabled
  player.danmaku[preferences.enabled ? 'show' : 'hide']()
  player.danmaku.unlimit(preferences.quantity === 'unlimited')
  if (showDanmaku)
    listen(showDanmaku, 'click', () => {
      queueMicrotask(() => {
        preferences.enabled = Boolean(showInput?.checked)
        save()
      })
    })

  const opacityItem = root.querySelector<HTMLElement>('.dplayer-setting-danmaku')
  opacityItem?.classList.add('dplayer-setting-danmaku-active')
  opacityItem?.classList.add('yla-setting-range-item')
  const opacityLabel = opacityItem?.querySelector<HTMLElement>('.dplayer-label')
  let opacityValue: HTMLElement | null = null
  if (opacityItem && opacityLabel) {
    opacityValue = document.createElement('span')
    opacityValue.className = 'yla-setting-current-value yla-opacity-value'
    opacityValue.textContent = `${Math.round(preferences.opacity * 100)}%`
    opacityItem.insertBefore(opacityValue, opacityItem.querySelector('.dplayer-danmaku-bar-wrap'))
  }
  const originalOpacityBar = opacityItem?.querySelector<HTMLElement>('.dplayer-danmaku-bar-wrap')
  const opacityBar = originalOpacityBar?.cloneNode(true) as HTMLElement | undefined
  if (originalOpacityBar && opacityBar instanceof HTMLElement) originalOpacityBar.replaceWith(opacityBar)
  const opacitySliderItem = opacityItem
  if (opacitySliderItem && opacityValue) {
    const opacityRangeItem = opacitySliderItem
    range(
      opacityRangeItem,
      0,
      100,
      Math.round(preferences.opacity * 100),
      (value) => `${value}%`,
      (value) => {
        preferences.opacity = value / 100
        player.danmaku.opacity(preferences.opacity)
        root.style.setProperty('--danmaku-opacity', String(preferences.opacity))
        save()
      },
    )
  }
  player.danmaku.opacity(preferences.opacity)
  player.on('danmaku_opacity', (value) => {
    if (typeof value === 'number') {
      preferences.opacity = value
      root.style.setProperty('--danmaku-opacity', String(value))
      if (opacityValue) opacityValue.textContent = `${Math.round(value * 100)}%`
      opacityBar?.setAttribute('aria-valuenow', `${Math.round(value * 100)}`)
      save()
    }
  })

  const loopItem = root.querySelector<HTMLElement>('.dplayer-setting-loop')
  const loopInput = root.querySelector<HTMLInputElement>('.dplayer-toggle-setting-input')
  if (loopInput) loopInput.checked = preferences.loop
  player.video.loop = preferences.loop
  if (loopItem)
    listen(loopItem, 'click', () => {
      queueMicrotask(() => {
        preferences.loop = Boolean(loopInput?.checked)
        player.video.loop = preferences.loop
        save()
      })
    })
  player.speed(preferences.playbackRate)
  const speedSetting = root.querySelector<HTMLElement>('.dplayer-setting-speed')
  if (speedSetting) {
    const value = document.createElement('span')
    value.className = 'yla-setting-current-value yla-playback-rate-value'
    value.textContent = `${preferences.playbackRate.toFixed(1)}×`
    const arrow = speedSetting.querySelector('.dplayer-toggle')
    speedSetting.insertBefore(value, arrow)
  }
  root.querySelectorAll<HTMLElement>('.dplayer-setting-speed-item').forEach((item) => {
    const rate = Number(item.dataset.speed)
    const selected = Number.isFinite(rate) && rate === preferences.playbackRate
    item.classList.toggle('yla-selected', selected)
    item.setAttribute('aria-selected', String(selected))
    listen(item, 'click', () => {
      const selectedRate = Number(item.dataset.speed)
      if (Number.isFinite(selectedRate)) {
        preferences.playbackRate = selectedRate
        const rateLabel = item.querySelector<HTMLElement>('.dplayer-label')?.textContent?.trim()
        const display = speedSetting?.querySelector<HTMLElement>('.yla-playback-rate-value')
        if (display) display.textContent = rateLabel || `${selectedRate.toFixed(1)}×`
        root.querySelectorAll<HTMLElement>('.dplayer-setting-speed-item').forEach((candidate) => {
          const isSelected = candidate === item
          candidate.classList.toggle('yla-selected', isSelected)
          candidate.setAttribute('aria-selected', String(isSelected))
        })
        save()
      }
    })
  })

  const deleted = makeSwitchItem('削除済みを表示', 'yla-setting-deleted', preferences.includeDeleted)
  const deletedInput = deleted.querySelector<HTMLInputElement>('input')!
  listen(deleted, 'click', (event) => {
    event.preventDefault()
    deletedInput.checked = !deletedInput.checked
    preferences.includeDeleted = deletedInput.checked
    save()
  })
  origin.append(deleted)
  if (options.live) {
    const follow = makeSwitchItem('ライブに追従', 'yla-setting-live-follow', preferences.liveFollow)
    const followInput = follow.querySelector<HTMLInputElement>('input')!
    listen(follow, 'click', (event) => {
      event.preventDefault()
      followInput.checked = !followInput.checked
      preferences.liveFollow = followInput.checked
      save()
      if (followInput.checked) options.onFollowLive()
    })
    origin.append(follow)
    const leftIcons = root.querySelector<HTMLElement>('.dplayer-icons-left')
    const badge = root.querySelector<HTMLElement>('.dplayer-live-badge') ?? document.createElement('span')
    if (!badge.parentElement) {
      badge.className = 'dplayer-live-badge'
      const dot = document.createElement('span')
      dot.className = 'dplayer-live-dot'
      dot.setAttribute('aria-hidden', 'true')
      dot.style.background = '#1681a5'
      badge.append(dot, document.createTextNode('ライブ'))
      leftIcons?.append(badge)
    }
    badge.tabIndex = 0
    badge.setAttribute('role', 'button')
    badge.setAttribute('aria-label', 'ライブ位置へ移動')
    listen(badge, 'click', options.onFollowLive)
    listen(badge, 'keydown', (event) => {
      if ((event as KeyboardEvent).key === 'Enter') options.onFollowLive()
    })
  }
  root.querySelector('.dplayer-comment-box')?.setAttribute('hidden', '')
  root.querySelector('.dplayer-comment')?.setAttribute('hidden', '')
  root.style.setProperty('--danmaku-opacity', String(preferences.opacity))
  root.style.setProperty('--danmaku-area', `${preferences.area}%`)
  root.style.setProperty('--danmaku-size', `${preferences.font}px`)
  root.style.setProperty('--danmaku-speed', `${16 / preferences.speed}s`)
  root.classList.toggle('danmaku-off', !preferences.enabled)
  const setDanmakuEnabled = (enabled: boolean) => {
    preferences.enabled = enabled
    const input = root.querySelector<HTMLInputElement>('.dplayer-showdan-setting-input')
    if (input) input.checked = enabled
    player.danmaku[enabled ? 'show' : 'hide']()
    root.classList.toggle('danmaku-off', !enabled)
    save()
  }

  return {
    setDanmakuEnabled,
    setLiveDelay(seconds) {
      const badge = root.querySelector<HTMLElement>('.dplayer-live-badge')
      if (!badge) return
      const delayed = seconds !== null && seconds >= 3
      badge.classList.toggle('yla-live-delayed', delayed)
      const label = badge.querySelector<HTMLElement>('.yla-live-delay') ?? document.createElement('span')
      label.className = 'yla-live-delay'
      label.textContent = delayed ? ` −${Math.floor(seconds)}秒` : ''
      if (!label.parentElement) badge.append(label)
    },
    dispose() {
      for (const remove of bag.splice(0)) remove()
    },
  }
}
