import { mkdir } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { expect, test } from './helpers'

test('DPlayer settings stay readable inside the player', async ({ page }, testInfo) => {
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('朝までまったりお話しする枠')
  await page
    .locator('.video-row')
    .filter({ hasText: '朝までまったりお話しする枠' })
    .getByRole('button', { name: '再生', exact: true })
    .click()

  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const player = dialog.locator('.dplayer')
  const panel = dialog.locator('.dplayer-setting-box')
  await dialog.locator('.dplayer-setting-icon').click()
  await expect(panel).toHaveClass(/dplayer-setting-box-open/)
  await page.waitForTimeout(400)
  await expect(panel).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)')

  const playerBounds = await player.evaluate((element) => element.getBoundingClientRect().toJSON())
  const panelBounds = await panel.evaluate((element) => element.getBoundingClientRect().toJSON())
  expect(panelBounds.height).toBeGreaterThan(100)
  expect(panelBounds.x).toBeGreaterThanOrEqual(playerBounds.x)
  expect(panelBounds.y).toBeGreaterThanOrEqual(playerBounds.y)
  expect(panelBounds.x + panelBounds.width).toBeLessThanOrEqual(playerBounds.x + playerBounds.width)
  expect(panelBounds.y + panelBounds.height).toBeLessThanOrEqual(playerBounds.y + playerBounds.height)

  const screenshotDir = process.env.YLA_SHOT_DIR
  if (screenshotDir) {
    const projectName = testInfo.project.name
    const screenshotName = projectName === 'webkit-iphone' ? 'webkit-iphone' : projectName
    const screenshotPath = join(resolve(screenshotDir), `${screenshotName}-live-player-settings.png`)
    await mkdir(dirname(screenshotPath), { recursive: true })
    await page.screenshot({ path: screenshotPath })
  }

  const items = panel.locator('.dplayer-setting-item:visible')
  expect(await items.count()).toBeGreaterThan(0)
  for (let index = 0; index < (await items.count()); index += 1) {
    const item = items.nth(index)
    await item.scrollIntoViewIfNeeded()
    const bounds = await item.evaluate((element) => element.getBoundingClientRect().toJSON())
    const fontSize = Number.parseFloat(await item.evaluate((element) => getComputedStyle(element).fontSize))
    expect(bounds.height, `menu item ${index} height`).toBeGreaterThanOrEqual(32)
    expect(fontSize, `menu item ${index} font size`).toBeGreaterThanOrEqual(12)
    expect(bounds.x, `menu item ${index} left`).toBeGreaterThanOrEqual(playerBounds.x)
    expect(bounds.y, `menu item ${index} top`).toBeGreaterThanOrEqual(playerBounds.y)
    expect(bounds.x + bounds.width, `menu item ${index} right`).toBeLessThanOrEqual(
      playerBounds.x + playerBounds.width,
    )
    expect(bounds.y + bounds.height, `menu item ${index} bottom`).toBeLessThanOrEqual(
      playerBounds.y + playerBounds.height,
    )
  }

  await panel.evaluate((element) => {
    element.scrollTop = 0
  })
})

test('large opening gap label stays on one line at 390px', async ({ page }, testInfo) => {
  test.skip((page.viewportSize()?.width ?? 0) !== 390)
  await page.route('**/api/videos*', async (route) => {
    const response = await route.fetch()
    const payload: unknown = await response.json()
    const patchVideo = (video: Record<string, unknown>) => ({
      ...video,
      recording_start_offset_ms: 4 * 365 * 24 * 60 * 60 * 1000,
      live_from_start: 'fallback',
    })
    const body = Array.isArray(payload)
      ? payload.map((video) => (typeof video === 'object' && video !== null ? patchVideo(video) : video))
      : typeof payload === 'object' && payload !== null && 'title' in payload
        ? patchVideo(payload as Record<string, unknown>)
        : payload
    await route.fulfill({ response, json: body })
  })

  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('朝までまったりお話しする枠')
  await page
    .locator('.video-row')
    .filter({ hasText: '朝までまったりお話しする枠' })
    .getByRole('button', { name: '再生', exact: true })
    .click()

  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const label = dialog.locator('.dialog-header small').filter({ hasText: '冒頭 約 4 年欠け' })
  await expect(label).toBeVisible()
  const lineCount = await label.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    return range.getClientRects().length
  })
  expect(lineCount).toBe(1)
  const output = process.env.YLA_SHOT_DIR
  if (output) {
    const projectName = testInfo.project.name === 'webkit-iphone' ? 'webkit-iphone' : 'mobile'
    const path = join(resolve(output), `${projectName}-opening-gap-label.png`)
    await mkdir(dirname(path), { recursive: true })
    await page.screenshot({ path })
  }
})
