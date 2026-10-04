import { expect, test } from './helpers'

test('player sliders and fullscreen controls stay usable inside the player', async ({ page }) => {
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('朝までまったりお話しする枠')
  await page
    .locator('.video-row')
    .filter({ hasText: '朝までまったりお話しする枠' })
    .getByRole('button', { name: '再生', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const player = dialog.locator('.dplayer')
  const settingsButton = dialog.locator('.dplayer-setting-icon')
  await settingsButton.click()
  const panel = dialog.locator('.dplayer-setting-box')
  await expect(panel).toHaveClass(/dplayer-setting-box-open/)
  await expect(panel).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)')
  await expect
    .poll(() => settingsButton.evaluate((element) => getComputedStyle(element, '::after').display))
    .toBe('none')
  const video = player.locator('video')
  await video.evaluate((element) => (element as HTMLVideoElement).pause())

  const sliders = [
    { selector: '.dplayer-setting-danmaku .dplayer-danmaku-bar-wrap', min: 0, max: 100 },
    { selector: '.yla-setting-flow-speed .dplayer-danmaku-bar-wrap', min: 5, max: 30 },
    { selector: '.yla-setting-font-size .dplayer-danmaku-bar-wrap', min: 12, max: 32 },
  ]
  for (const slider of sliders) {
    const bar = panel.locator(slider.selector)
    for (const fraction of [0.25, 0.5, 0.75]) {
      const bounds = (await bar.boundingBox())!
      await bar.click({ position: { x: bounds.width * fraction, y: bounds.height / 2 } })
      const value = Number(await bar.getAttribute('aria-valuenow'))
      const actualFraction = (value - slider.min) / (slider.max - slider.min)
      expect(Math.abs(actualFraction - fraction) * 100).toBeLessThanOrEqual(3)
    }
    const bounds = (await bar.boundingBox())!
    await page.mouse.move(bounds.x + bounds.width * 0.2, bounds.y + bounds.height / 2)
    await page.mouse.down()
    await page.mouse.move(bounds.x + bounds.width * 0.8, bounds.y + bounds.height / 2 + 30, { steps: 8 })
    await page.mouse.up()
    const value = Number(await bar.getAttribute('aria-valuenow'))
    expect(Math.abs(((value - slider.min) / (slider.max - slider.min)) * 100 - 80)).toBeLessThanOrEqual(3)
    await expect(panel).toHaveClass(/dplayer-setting-box-open/)
    await bar.focus()
    const before = Number(await bar.getAttribute('aria-valuenow'))
    await page.keyboard.press('ArrowRight')
    await expect(bar).toHaveAttribute('aria-valuenow', String(Math.min(slider.max, before + 1)))
    await page.keyboard.press('ArrowLeft')
    await expect(bar).toHaveAttribute('aria-valuenow', String(before))
    await page.keyboard.press('Home')
    await expect(bar).toHaveAttribute('aria-valuenow', String(slider.min))
    await page.keyboard.press('End')
    await expect(bar).toHaveAttribute('aria-valuenow', String(slider.max))
  }
  await expect(video).toHaveJSProperty('paused', true)

  if ((page.viewportSize()?.width ?? 1440) <= 620) {
    const panelBounds = await panel.boundingBox()
    const playerBounds = await player.boundingBox()
    expect(panelBounds).not.toBeNull()
    expect(playerBounds).not.toBeNull()
    expect(panelBounds!.y + panelBounds!.height).toBeLessThanOrEqual(playerBounds!.y + playerBounds!.height)
    await panel.evaluate((element) => {
      element.scrollTop = element.scrollHeight
    })
    const deletedToggle = panel.locator('.yla-setting-deleted')
    await deletedToggle.click()
    await expect(deletedToggle.locator('input')).toBeChecked()
    const fontSlider = panel.locator('.yla-setting-font-size .dplayer-danmaku-bar-wrap')
    await fontSlider.click({ position: { x: 0.6 * (await fontSlider.boundingBox())!.width, y: 12 } })
    await expect(fontSlider).toHaveAttribute('aria-valuenow', '24')
  }
  await page.keyboard.press('Escape')

  const controller = player.locator('.dplayer-controller')
  const rightIcons = controller.locator('.dplayer-icons-right')
  const airplay = rightIcons.locator('.dplayer-airplay-icon')
  const settings = rightIcons.locator('.dplayer-setting')
  const webFull = controller.locator('.dplayer-full-in-icon')
  const full = controller.locator('.dplayer-full-icon')
  const rects = await Promise.all([
    airplay.boundingBox(),
    settings.boundingBox(),
    webFull.boundingBox(),
    full.boundingBox(),
    controller.boundingBox(),
  ])
  expect(rects.slice(0, 4).every((rect) => rect !== null)).toBe(true)
  const gaps = [
    rects[1]!.x - rects[0]!.x - rects[0]!.width,
    rects[2]!.x - rects[1]!.x - rects[1]!.width,
    rects[3]!.x - rects[2]!.x - rects[2]!.width,
  ]
  expect(Math.max(...gaps) - Math.min(...gaps)).toBeLessThanOrEqual(1)
  for (const rect of rects.slice(0, 4)) {
    expect(rect!.width).toBeGreaterThan(0)
    expect(rect!.height).toBeGreaterThan(0)
    expect(rect!.x).toBeGreaterThanOrEqual(rects[4]!.x)
    expect(rect!.x + rect!.width).toBeLessThanOrEqual(rects[4]!.x + rects[4]!.width)
  }
  await webFull.click()
  await expect(player).toHaveClass(/dplayer-fulled/)
  await webFull.click()
  await expect(player).not.toHaveClass(/dplayer-fulled/)
  await full.click()
  await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true)
  await full.click()
  await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true)
})
