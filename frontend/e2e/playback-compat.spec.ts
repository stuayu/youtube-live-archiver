import { expect, test } from './helpers'

test('H.264 MKV is remuxed before playback in desktop engines', async ({ page, browserName }, testInfo) => {
  test.skip(!['desktop', 'webkit'].includes(testInfo.project.name))
  const requests: string[] = []
  page.on('request', (request) => {
    requests.push(request.url())
  })
  await page.goto('/?tab=videos')
  const row = page.locator('.video-row').filter({ hasText: '歌枠 / Singing Stream' })
  await row.getByRole('button', { name: '再生', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  await expect(dialog.locator('.playback-status')).toContainText(/再生用ファイルを準備しています/, {
    timeout: 10000,
  })
  const video = dialog.locator('video')
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime), {
      timeout: 30000,
    })
    .toBeGreaterThan(0.5)
  expect(requests.some((url) => /\/file\?playable=true/.test(url))).toBe(true)
  expect(requests.some((url) => /\/file(?:\?|$)/.test(url) && !url.includes('playable=true'))).toBe(false)
  expect(requests.some((url) => /youtube\.com|ytimg\.com|googlevideo\.com/.test(url))).toBe(false)
  expect(browserName).toMatch(/chromium|webkit/)
})

test('AV1 and Opus offers an explicit H.264 conversion in WebKit', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'webkit')
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('AV1 / Opus browser compatibility fixture')
  const row = page.locator('.video-row').filter({ hasText: 'AV1 / Opus browser compatibility fixture' })
  await expect(row)
    .toBeVisible({ timeout: 5000 })
    .catch(() => {})
  test.skip((await row.count()) === 0, 'ffmpeg AV1 / Opus encoder unavailable')
  await row.getByRole('button', { name: '再生', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  test.setTimeout(180000)
  const status = dialog.locator('.player-stage .playback-status')
  await expect(status).toContainText(/このブラウザでは再生できない形式です/)
  await page.waitForTimeout(16000)
  await expect(status).toContainText(/このブラウザでは再生できない形式です/)
  await expect(status.getByRole('button', { name: '再試行' })).toHaveCount(0)
  await status.getByRole('button', { name: '変換して再生' }).click()
  await expect(status).toContainText(/H\.264 に変換中です/)
  await expect(status.getByRole('button')).toHaveCount(0)
  await expect
    .poll(() => dialog.locator('video').evaluate((element) => (element as HTMLVideoElement).currentTime), {
      timeout: 120000,
    })
    .toBeGreaterThan(0.5)
})

test('broken media gets an in-player recovery notice within fifteen seconds', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'webkit')
  await page.goto('/?tab=videos')
  const row = page.locator('.video-row').filter({ hasText: '壊れたメディア timeout 案内確認' })
  const startedAt = Date.now()
  await row.getByRole('button', { name: '再生', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  await expect(dialog.locator('.playback-status')).toContainText(/動画を読み込めません/, {
    timeout: 18000,
  })
  expect(Date.now() - startedAt).toBeLessThan(16000)
  const actions = dialog.locator('.player-stage .playback-status .playback-actions')
  await expect(actions.getByRole('button', { name: '再試行' })).toBeVisible()
  await expect(actions.getByRole('button', { name: '変換して再生' })).toBeVisible()
  for (const name of ['別の形式で再生', '再試行']) {
    const [color, background] = await actions.getByRole('button', { name }).evaluate((element) => {
      const style = getComputedStyle(element)
      return [style.color, style.backgroundColor]
    })
    expect(color, `${name} の文字色が背景と同じ`).not.toBe(background)
  }
  await expect(dialog.locator('.playback-actions')).toHaveCount(1)
})

test('a loaded video that the browser refuses to autoplay is not reported as a failure', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'webkit')
  test.setTimeout(90000)
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.reject(new DOMException('blocked', 'NotAllowedError'))
  })
  await page.goto('/?tab=videos')
  const row = page.locator('.video-row').filter({ hasText: '歌枠 / Singing Stream' })
  await row.getByRole('button', { name: '再生', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const video = dialog.locator('video')
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).readyState), { timeout: 30000 })
    .toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(16000)
  await expect(dialog.locator('.playback-status')).toHaveCount(0)
  expect(await video.evaluate((element) => (element as HTMLVideoElement).paused)).toBe(true)
})

test('converting from the info panel keeps the running player on screen', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop')
  test.setTimeout(120000)
  await page.goto('/?tab=videos')
  const row = page.locator('.video-row').filter({ hasText: '歌枠 / Singing Stream' })
  await row.getByRole('button', { name: '再生', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const video = dialog.locator('video')
  const currentTime = () => video.evaluate((element) => (element as HTMLVideoElement).currentTime)
  await expect.poll(currentTime, { timeout: 30000 }).toBeGreaterThan(0.2)
  await expect(dialog.locator('.playback-status')).toHaveCount(0)
  await video.evaluate((element) => element.setAttribute('data-e2e-instance', 'before-conversion'))
  await dialog.locator('.player-desktop-info').getByRole('button', { name: 'H.264 変換' }).click()
  await expect(dialog.locator('.player-desktop-info [role="status"]')).toContainText(/H\.264 変換/)
  await expect(dialog.locator('.playback-status')).toHaveCount(0)
  await expect(dialog.locator('.player-desktop-info [role="status"]')).toHaveText(
    'H.264 変換が完了しました',
    {
      timeout: 90000,
    },
  )
  await expect(dialog.locator('.playback-status')).toHaveCount(0)
  await expect(video).toHaveAttribute('data-e2e-instance', 'before-conversion')
})

test('danmaku filters reload with a valid address and no 404', async ({ page }, testInfo) => {
  test.skip(!['desktop', 'webkit'].includes(testInfo.project.name))
  const failures: string[] = []
  const danmaku: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/chat/danmaku/')) danmaku.push(request.url())
    if (request.url().includes('undefined')) failures.push(request.url())
  })
  page.on('response', (response) => {
    if (response.status() === 404 && response.url().includes('danmaku')) failures.push(response.url())
  })
  await page.goto('/?tab=videos')
  const row = page.locator('.video-row').filter({ hasText: '歌枠 / Singing Stream' })
  await row.getByRole('button', { name: '再生', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  await expect
    .poll(() => danmaku.some((url) => url.includes('/chat/danmaku/all-0-window-0-180000/v3/')))
    .toBe(true)
  await dialog.getByLabel('種別').selectOption('membership')
  await dialog.getByRole('checkbox', { name: '削除済みを表示' }).last().check()
  await expect
    .poll(() => danmaku.some((url) => url.includes('/chat/danmaku/membership-1-window-0-180000/v3/')))
    .toBe(true)
  expect(failures).toEqual([])
})
