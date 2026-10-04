declare module 'dplayer' {
  // The constructor takes the endpoint as `api`; DPlayer keeps it as `api.address`,
  // and `danmaku.reload()` replaces that internal object.
  interface DanmakuOptions {
    id: string
    api: string
    token: string
    maximum: number
    addition: unknown[]
    user: string
    bottom: number
    unlimited: boolean
    mutex: boolean
    speedRate: number
  }
  type DanmakuReloadOptions = Omit<DanmakuOptions, 'api'> & { address: string }
  interface DPlayerOptions {
    container: HTMLElement
    lang: string
    theme: string
    autoplay: boolean
    loop?: boolean
    hotkey?: boolean
    video: { url: string; type?: string; customType?: Record<string, (video: HTMLVideoElement) => void> }
    danmaku?: DanmakuOptions
    contextmenu?: unknown[]
    highlight?: unknown[]
  }
  export default class DPlayer {
    constructor(options: DPlayerOptions)
    readonly video: HTMLVideoElement
    readonly danmaku: {
      reload(options: DanmakuReloadOptions): void
      speed(rate: number): void
      show(): void
      hide(): void
      opacity(value?: number): number | void
      unlimit(value: boolean): void
    }
    speed(value: number): void
    on(name: string, callback: (value?: number) => void): void
    destroy(): void
  }
}
