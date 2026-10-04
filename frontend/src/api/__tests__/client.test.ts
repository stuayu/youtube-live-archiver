import { afterEach, describe, expect, it, vi } from 'vitest'
import { databaseUnavailable, request } from '../client'
describe('API client', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('normalizes backend Japanese error and field errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: '保存できません', fields: { url: 'URL が必要です' } }), {
          status: 422,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )
    await expect(request('/x')).rejects.toMatchObject({
      message: '保存できません',
      status: 422,
      fields: { url: 'URL が必要です' },
    })
  })
  it('handles non JSON error responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('upstream failed', { status: 502 })))
    await expect(request('/x')).rejects.toMatchObject({ message: 'upstream failed', status: 502 })
  })
  it('handles network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(request('/x')).rejects.toMatchObject({ message: '通信に失敗しました: offline', status: 0 })
  })
  it('tracks database disconnect separately from API network failures', async () => {
    databaseUnavailable.value = false
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: 'データベースに接続できません' }), {
          status: 503,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )
    await expect(request('/status')).rejects.toThrow('データベースに接続できません')
    expect(databaseUnavailable.value).toBe(true)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 200 })))
    await request('/health')
    expect(databaseUnavailable.value).toBe(false)
  })
})
