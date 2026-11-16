import { describe, expect, it, vi } from 'vitest'
import { loadPanelsIndependently, panel } from './panelLoading'

describe('loadPanelsIndependently', () => {
  it('applies a fast panel without waiting for a slow one', async () => {
    let releaseChat: (value: string) => void = () => {}
    const chat = new Promise<string>((resolve) => (releaseChat = resolve))
    const applySummary = vi.fn()
    const applyChat = vi.fn()
    const done = loadPanelsIndependently(
      [panel(() => chat, applyChat, true), panel(() => Promise.resolve('summary'), applySummary)],
      vi.fn(),
    )
    await Promise.resolve()
    await Promise.resolve()
    // チャットが遅くても要約は先に表示される。
    expect(applySummary).toHaveBeenCalledWith('summary')
    expect(applyChat).not.toHaveBeenCalled()
    releaseChat('chat')
    await done
    expect(applyChat).toHaveBeenCalledWith('chat')
  })

  it('reports errors only for panels that opt in', async () => {
    const onError = vi.fn()
    await loadPanelsIndependently(
      [
        panel(() => Promise.reject(new Error('チャット失敗')), undefined, true),
        panel(() => Promise.reject(new Error('要約失敗'))),
      ],
      onError,
    )
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith('チャット失敗')
  })

  it('uses a fallback message for non-Error rejections', async () => {
    const onError = vi.fn()
    await loadPanelsIndependently([panel(() => Promise.reject('x'), undefined, true)], onError)
    expect(onError).toHaveBeenCalledWith('情報を取得できません')
  })
})
