import { expect, test } from './helpers'

test('player tabs, video geometry, seeking, Escape cleanup', async ({ page }) => {
  await page.addInitScript(() => {
    const active = new Set<number>()
    const nativeSet: typeof window.setInterval = window.setInterval.bind(window)
    const nativeClear: typeof window.clearInterval = window.clearInterval.bind(window)
    Object.defineProperty(window, '__ylaActiveIntervals', { value: active })
    window.setInterval = function (...args: Parameters<typeof window.setInterval>) {
      const id = nativeSet(...args)
      active.add(id)
      return id
    }
    window.clearInterval = function (id?: number) {
      if (id !== undefined) active.delete(id)
      nativeClear(id)
    }
  })
  await page.goto('/?tab=videos')
  const initialIntervals = await page.evaluate(
    () => (window as Window & { __ylaActiveIntervals: Set<number> }).__ylaActiveIntervals.size,
  )
  await page.getByPlaceholder('タイトル・説明').fill('朝までまったりお話しする枠')
  await page
    .locator('.video-row')
    .filter({ hasText: '朝までまったりお話しする枠' })
    .getByRole('button', { name: '再生', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const video = dialog.locator('video')
  await expect(video).toBeVisible({ timeout: 15000 })
  const bounds = await video.boundingBox()
  const viewport = page.viewportSize()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.y).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport!.width)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport!.height)
  const panelTabs = ['チャット', '文字起こし', '要約', 'タイムライン']
  if (viewport!.width < 901) panelTabs.push('情報')
  for (const label of panelTabs) {
    await dialog.getByRole('button', { name: label, exact: true }).click()
    await expect(dialog.locator('.panel-content')).toBeVisible()
  }
  await dialog.getByRole('button', { name: '文字起こし', exact: true }).click()
  await expect(dialog.locator('.transcript-row').first()).toBeVisible()
  const before = await video.evaluate((element) => (element as HTMLVideoElement).currentTime)
  await dialog.locator('.transcript-row').nth(1).click()
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime), { timeout: 5000 })
    .toBeGreaterThan(before)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('.dplayer-mount')).toHaveCount(0)
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as Window & { __ylaActiveIntervals: Set<number> }).__ylaActiveIntervals.size,
      ),
    )
    .toBe(initialIntervals)
})
