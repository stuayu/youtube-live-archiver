import { expect, test } from './helpers'

test('unavailable chat replay shows coverage and retrieves it in background', async ({ page }) => {
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('歌枠 / Singing Stream')
  await page
    .locator('.video-row')
    .filter({ hasText: '歌枠 / Singing Stream' })
    .getByRole('button', { name: '再生', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  await dialog.getByRole('button', { name: 'チャット', exact: true }).click()
  await expect(dialog).toContainText('この時刻のコメントは未取得です')
  await expect(dialog).toContainText('取得済み: 0:00:06 〜 0:00:08')
  await dialog.getByRole('button', { name: '取得済みの位置へ移動' }).click()
  await expect(dialog.locator('.chat-message')).toContainText('後半だけのコメント')
  await dialog.getByRole('button', { name: '今すぐ再取得' }).click()
  await expect(dialog).toContainText('取得中…')
  await expect(dialog).toContainText('先頭のコメント', { timeout: 15000 })
})

test('player danmaku preferences and replay chat stay inside the player', async ({ page }) => {
  await page.route('**/api/chat-assets/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#00aacc"/></svg>',
    }),
  )
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('朝までまったりお話しする枠')
  await page
    .locator('.video-row')
    .filter({ hasText: '朝までまったりお話しする枠' })
    .getByRole('button', { name: '再生', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const settings = dialog.locator('.dplayer-setting-icon')
  await expect(settings).toBeVisible()
  const controlBar = dialog.locator('.dplayer-icons-right')
  await expect(controlBar.locator('.dplayer-setting-icon')).toHaveCount(1)
  await expect(controlBar.locator('.dplayer-full-icon')).toHaveCount(1)
  await expect(controlBar.locator('.dplayer-full-in-icon')).toHaveCount(1)
  await expect(dialog.locator('.dplayer-comment')).toBeHidden()
  await expect(dialog.locator('.player-control-button, .player-settings-panel')).toHaveCount(0)
  await expect(dialog.locator('.dplayer').locator('..')).toHaveClass(/player-stage/)
  await expect(dialog.locator('.player-video-info')).toHaveCount(1)
  await expect(dialog.locator('.player-video-info').locator('..')).not.toHaveClass(/player-stage/)
  await settings.click()
  const settingsPanel = dialog.locator('.dplayer-setting-box')
  await expect(settingsPanel).toBeVisible()
  await expect(settingsPanel).toHaveCSS('width', '220px')
  await expect(settingsPanel).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)')
  await expect(settingsPanel).toHaveCSS('overflow-y', 'auto')
  await expect(settingsPanel).toHaveCSS('scrollbar-width', 'thin')
  await expect(settingsPanel).toContainText('再生速度')
  await expect(settingsPanel).not.toContainText('Speed')
  for (const label of ['流れる速さ', '文字サイズ', '表示領域', '弾幕の量', 'コメント種別', '削除済みを表示'])
    await expect(settingsPanel.locator('.dplayer-setting-item').filter({ hasText: label })).toBeVisible()
  const panelWidth = await settingsPanel.evaluate((element) => element.getBoundingClientRect().width)
  expect(panelWidth).toBeGreaterThanOrEqual(200)
  expect(panelWidth).toBeLessThanOrEqual(240)
  const playbackRate = settingsPanel.locator('.yla-playback-rate-value')
  await expect(playbackRate).toHaveText('1.0×')
  expect((await playbackRate.boundingBox())?.width).toBeGreaterThan(20)
  const playbackRateBounds = (await playbackRate.boundingBox())!
  const playbackArrowBounds = (await settingsPanel
    .locator('.dplayer-setting-speed > .dplayer-toggle')
    .boundingBox())!
  expect(playbackRateBounds.x + playbackRateBounds.width).toBeLessThanOrEqual(playbackArrowBounds.x)
  for (const selector of ['.yla-setting-area', '.yla-setting-quantity', '.yla-setting-danmakuKind']) {
    const item = settingsPanel.locator(selector)
    const value = await item.locator('.yla-setting-value').boundingBox()
    const arrow = await item.locator('.yla-setting-arrow').boundingBox()
    expect(value).not.toBeNull()
    expect(arrow).not.toBeNull()
    expect(value!.x + value!.width).toBeLessThanOrEqual(arrow!.x)
  }
  for (const selector of [
    '.yla-setting-flow-speed .dplayer-danmaku-bar-wrap',
    '.yla-setting-font-size .dplayer-danmaku-bar-wrap',
    '.dplayer-setting-danmaku .dplayer-danmaku-bar-wrap',
  ]) {
    const bounds = await settingsPanel.locator(selector).boundingBox()
    expect(bounds?.width).toBeGreaterThan(50)
    expect(bounds?.height).toBeGreaterThan(12)
  }
  const rangeRects = await Promise.all(
    [
      '.yla-setting-flow-speed .dplayer-danmaku-bar',
      '.yla-setting-font-size .dplayer-danmaku-bar',
      '.dplayer-setting-danmaku .dplayer-danmaku-bar',
    ].map(async (selector) => settingsPanel.locator(selector).boundingBox()),
  )
  expect(
    Math.max(...rangeRects.map((rect) => rect?.x ?? 0)) - Math.min(...rangeRects.map((rect) => rect?.x ?? 0)),
  ).toBeLessThanOrEqual(1)
  expect(
    Math.max(...rangeRects.map((rect) => rect?.height ?? 0)) -
      Math.min(...rangeRects.map((rect) => rect?.height ?? 0)),
  ).toBeLessThanOrEqual(1)
  const speedBar = settingsPanel.locator('.yla-setting-flow-speed .dplayer-danmaku-bar-wrap')
  const speedBounds = (await speedBar.boundingBox())!
  if (await page.evaluate(() => window.innerWidth <= 620))
    await speedBar.tap({ position: { x: 0.75 * speedBounds.width, y: 12 } })
  else await speedBar.click({ position: { x: 0.75 * speedBounds.width, y: 12 } })
  await expect(speedBar).toHaveAttribute('aria-valuenow', '24')
  await expect(dialog.locator('.dplayer-mount')).toHaveCSS('--danmaku-speed', `${16 / 24}s`)
  const fontBar = settingsPanel.locator('.yla-setting-font-size .dplayer-danmaku-bar-wrap')
  const fontBounds = (await fontBar.boundingBox())!
  if (await page.evaluate(() => window.innerWidth <= 620))
    await fontBar.tap({ position: { x: 0.75 * fontBounds.width, y: 12 } })
  else await fontBar.click({ position: { x: 0.75 * fontBounds.width, y: 12 } })
  await expect(fontBar).toHaveAttribute('aria-valuenow', '27')
  await expect(dialog.locator('.dplayer-mount')).toHaveCSS('--danmaku-size', '27px')
  await settingsPanel.locator('.yla-setting-area').click()
  await expect(settingsPanel).toHaveCSS('width', '220px')
  const fullArea = settingsPanel
    .locator('.yla-setting-choice-panel:visible .dplayer-setting-speed-item')
    .filter({ hasText: '全体' })
  await fullArea.click()
  await expect(settingsPanel.locator('.yla-setting-area .yla-setting-value')).toHaveText('全体')
  await settingsPanel.locator('.yla-setting-danmakuKind').click()
  const superchatKind = settingsPanel
    .locator('.yla-setting-choice-panel:visible .dplayer-setting-speed-item')
    .filter({ hasText: 'スーパーチャット' })
  await superchatKind.click()
  const currentLabel = settingsPanel.locator('.yla-setting-danmakuKind .yla-setting-value')
  await expect(currentLabel).toHaveText('スーパーチャット')
  const currentBounds = await currentLabel.boundingBox()
  expect(currentBounds?.width).toBeGreaterThan(30)
  expect(currentBounds?.height).toBeGreaterThan(10)
  const labelColor = await currentLabel.evaluate((element) => getComputedStyle(element).color)
  expect(labelColor).not.toBe('rgba(0, 0, 0, 0)')
  await expect(page.evaluate(() => localStorage.getItem('yla-danmaku'))).resolves.toContain('"speed":24')
  await expect(page.evaluate(() => localStorage.getItem('yla-danmaku'))).resolves.toContain('"font":27')
  await settingsPanel.locator('.yla-setting-danmakuKind').click()
  await settingsPanel
    .locator('.yla-setting-choice-panel:visible .dplayer-setting-speed-item')
    .filter({ hasText: 'すべて' })
    .click()
  const panelBox = await settingsPanel.boundingBox()
  const stageBox = await dialog.locator('.dplayer').boundingBox()
  expect(panelBox).not.toBeNull()
  expect(stageBox).not.toBeNull()
  expect(panelBox!.x).toBeGreaterThanOrEqual(stageBox!.x)
  expect(panelBox!.y).toBeGreaterThanOrEqual(stageBox!.y)
  expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(stageBox!.x + stageBox!.width)
  expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(stageBox!.y + stageBox!.height)
  await page.keyboard.press('Escape')
  await expect(settingsPanel).not.toHaveClass(/dplayer-setting-box-open/)
  await expect(dialog).toBeVisible()
  await settings.click()
  await expect(settingsPanel.locator('.dplayer-setting-showdan input')).toBeChecked()
  await settingsPanel.locator('.dplayer-setting-showdan').click()
  await expect(dialog.locator('.dplayer-mount')).toHaveClass(/danmaku-off/)
  await expect(page.evaluate(() => localStorage.getItem('yla-danmaku'))).resolves.toContain('"enabled":false')

  const video = dialog.locator('video')
  await video.evaluate((element) => {
    const target = element as HTMLVideoElement
    target.pause()
    target.currentTime = 11.5
    target.dispatchEvent(new Event('timeupdate'))
  })
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(6)
  await dialog.getByRole('button', { name: 'チャット', exact: true }).click()
  const emoteRow = dialog.locator('article.chat-message:has(.chat-emote[alt=":_seed:"])')
  await expect(emoteRow).toBeVisible({ timeout: 8000 })
  await expect(emoteRow.locator('.chat-author-line > .chat-author-group')).toBeVisible()
  await expect(emoteRow.locator('.chat-author-line > .chat-time')).toBeVisible()
  await expect(dialog.locator('.chat-message.superchat').filter({ hasText: '¥1,000' })).toBeVisible()
  await expect(
    dialog.locator('.chat-message.membership').filter({ hasText: 'メンバー 45 か月' }),
  ).toBeVisible()
  await expect(dialog.locator('.chat-emote')).toBeVisible()
  await expect(emoteRow.locator('.chat-body')).not.toContainText(':_seed:')
  if (await page.evaluate(() => window.innerWidth <= 620)) {
    await expect
      .poll(() =>
        dialog.locator('.chat-message').evaluateAll((rows) => {
          const bounds = document.querySelector('.chat-list')?.getBoundingClientRect()
          if (!bounds) return 0
          return rows.filter((row) => {
            const rect = row.getBoundingClientRect()
            return rect.top >= bounds.top && rect.bottom <= bounds.bottom
          }).length
        }),
      )
      .toBeGreaterThanOrEqual(5)
  }
  const timestamp = emoteRow.getByRole('button')
  await video.evaluate((element) => (element as HTMLVideoElement).pause())
  await timestamp.click()
  await video.evaluate((element) => (element as HTMLVideoElement).pause())
  const seekedTime = await video.evaluate((element) => (element as HTMLVideoElement).currentTime)
  expect(seekedTime).toBeGreaterThanOrEqual(2.5)
  expect(seekedTime).toBeLessThan(4)
  await expect(dialog.locator('.chat-message.deleted')).toHaveCount(0)
  await video.evaluate((element) => {
    const target = element as HTMLVideoElement
    target.pause()
    target.currentTime = 6.5
    target.dispatchEvent(new Event('timeupdate'))
  })
  if (await page.evaluate(() => window.innerWidth <= 620)) {
    await dialog.locator('.chat-menu summary').click()
    await dialog.locator('.chat-menu-deleted input').check()
  } else {
    await dialog.locator('.deleted-toggle input').check()
  }
  await expect(dialog.locator('.chat-message.deleted')).toHaveCount(1)

  await page.reload()
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('朝までまったりお話しする枠')
  await page
    .locator('.video-row')
    .filter({ hasText: '朝までまったりお話しする枠' })
    .getByRole('button', { name: '再生', exact: true })
    .click()
  const reopened = page.getByRole('dialog', { name: /再生:/ })
  await reopened.locator('.dplayer-setting-icon').click()
  await expect(reopened.locator('.dplayer-setting-showdan input')).not.toBeChecked()
  await expect(reopened.locator('.yla-setting-flow-speed .yla-setting-current-value')).toHaveText('24')
  await expect(reopened.locator('.yla-setting-font-size .yla-setting-current-value')).toHaveText('27px')
  await expect(reopened.locator('.yla-setting-area .yla-setting-value')).toHaveText('全体')
})

test('legacy raw chat rows render cached custom emoji and old string badges', async ({ page }) => {
  await page.route('**/api/chat-assets/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" fill="#00aacc"/></svg>',
    }),
  )
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('旧形式チャットの配信')
  await page
    .locator('.video-row')
    .filter({ hasText: '旧形式チャットの配信' })
    .getByRole('button', { name: '再生', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  await dialog.locator('video').evaluate((element) => {
    const video = element as HTMLVideoElement
    video.pause()
    video.currentTime = 4
    video.dispatchEvent(new Event('timeupdate'))
  })
  await expect(dialog.locator('.chat-message').filter({ hasText: '旧形式の投稿者' })).toBeVisible()
  const row = dialog.locator('.chat-message').filter({ hasText: '旧形式の投稿者' })
  await expect(row.locator('.chat-emote')).toBeVisible()
  await expect(row.locator('.author-badges img')).toBeVisible()
  await expect(row.locator('.chat-body')).not.toContainText(':_legacy:')
})
