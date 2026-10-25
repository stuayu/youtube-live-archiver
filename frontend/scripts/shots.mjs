import { chromium, webkit } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { startE2E, stopE2E } from './e2e-env.mjs'
const context = await startE2E()
let browser
const output = process.env.YLA_SHOT_DIR
  ? resolve(process.env.YLA_SHOT_DIR)
  : resolve('shots', process.argv[2] === 'review' ? 'review' : 'latest')
await mkdir(output, { recursive: true })
const screens = [
  ['home', 'ホーム'],
  ['channels', 'チャンネル'],
  ['upcoming', '配信予定'],
  ['videos', 'アーカイブ'],
  ['search', '全文検索'],
  ['storages', 'ストレージ'],
  ['analysis', '分析'],
  ['activity', 'アクティビティ'],
  ['settings', '設定'],
]
async function navigate(page, label, width) {
  if (width < 1024) {
    if (!['ホーム', 'チャンネル', '配信予定', 'アーカイブ'].includes(label)) {
      await page.locator('.mobile-tab-more .mobile-tab').click()
      await page.locator('.mobile-more-menu').getByRole('button', { name: label, exact: true }).click()
    } else await page.locator('.mobile-nav .mobile-tab').filter({ hasText: label }).click()
  } else await page.locator('.sidebar .nav-item').filter({ hasText: label }).click()
}
try {
  browser = await chromium.launch({ headless: true, args: ['--lang=ja-JP'] })
  for (const [device, width, height] of [
    ['desktop', 1440, 1000],
    ['tablet', 820, 1120],
    ['mobile', 390, 844],
  ]) {
    for (const color of ['light', 'dark']) {
      const page = await browser.newPage({
        viewport: { width, height },
        colorScheme: color,
        locale: 'ja-JP',
        timezoneId: 'Asia/Tokyo',
        isMobile: device === 'mobile',
        hasTouch: device === 'mobile',
      })
      await page.addInitScript((value) => {
        localStorage.setItem('yla-theme', value)
        document.documentElement.dataset.theme = value
      }, color)
      await page.route('**/api/chat-assets/**', (route) =>
        route.fulfill({
          status: 200,
          contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#00aacc"/></svg>',
        }),
      )
      await page.goto(context.baseURL, { waitUntil: 'networkidle' })
      for (const [tab, label] of screens) {
        await navigate(page, label, width)
        if (tab === 'upcoming' && device === 'desktop') {
          await page.getByRole('button', { name: 'URL から追加' }).click()
          await page.getByRole('dialog', { name: 'URL から動画を追加' }).waitFor()
          await page.screenshot({ path: join(output, `${device}-manual-video-dialog-${color}.png`) })
          await page.keyboard.press('Escape')
        }
        if (tab === 'upcoming' && device === 'mobile') {
          await page.getByRole('button', { name: 'URL から追加' }).click()
          await page.getByRole('dialog', { name: 'URL から動画を追加' }).waitFor()
          await page.screenshot({ path: join(output, `${device}-manual-video-dialog-${color}.png`) })
          await page.keyboard.press('Escape')
        }
        if (tab === 'videos') await page.locator('.video-row').first().waitFor({ state: 'visible' })
        await page.waitForTimeout(250)
        await page.screenshot({ path: join(output, `${device}-${tab}-${color}.png`) })
        if (tab === 'videos') {
          const play = page.getByRole('button', { name: '再生', exact: true }).first()
          if (await play.count()) {
            await play.click()
            await page
              .waitForFunction(
                () => {
                  const video = document.querySelector('.dplayer-mount video')
                  return video instanceof HTMLVideoElement && video.readyState >= 2
                },
                { timeout: 10000 },
              )
              .catch(() => {})
            await page.locator('.dplayer-mount video').evaluate((element) => {
              const video = element
              video.currentTime = 8.5
              void video.play().catch(() => {})
            })
            await page.waitForTimeout(600)
            await page.screenshot({ path: join(output, `${device}-player-${color}.png`) })
            if (width === 1440 || width === 820 || width === 390) {
              const fullScreen = page.locator('.dplayer-full-icon')
              await fullScreen.hover()
              await page.waitForTimeout(400)
              const controller = await page.locator('.dplayer-controller').boundingBox()
              const playerBox = await page.locator('.dplayer').boundingBox()
              if (controller && playerBox) {
                const clipY = Math.max(playerBox.y, controller.y - 80)
                await page.screenshot({
                  path: join(output, `${device}-player-controls-right-${color}.png`),
                  clip: {
                    x: Math.max(controller.x, controller.x + controller.width - 320),
                    y: clipY,
                    width: Math.min(320, controller.width),
                    height: Math.min(playerBox.y + playerBox.height - clipY, controller.height + 80),
                  },
                })
              }
              await page.locator('.dplayer-setting-icon').click()
              await page.locator('.dplayer-setting-box-open').waitFor()
              await page.waitForFunction(() => {
                const panel = document.querySelector('.dplayer-setting-box')
                if (!(panel instanceof HTMLElement) || !panel.classList.contains('dplayer-setting-box-open'))
                  return false
                const transform = new DOMMatrixReadOnly(getComputedStyle(panel).transform)
                return transform.a > 0.98 && panel.getBoundingClientRect().height > 0
              })
              await page.waitForTimeout(350)
              await page.screenshot({ path: join(output, `${device}-player-settings-${color}.png`) })
              await page.keyboard.press('Escape')
              await page.waitForFunction(
                () =>
                  !document
                    .querySelector('.dplayer-setting-box')
                    ?.classList.contains('dplayer-setting-box-open'),
              )
              await page.getByRole('button', { name: 'チャット', exact: true }).click()
              await page.waitForTimeout(300)
              await page.locator('.chat-list').evaluate((element) => {
                element.scrollTop = 0
              })
              await page.screenshot({ path: join(output, `${device}-player-superchat-${color}.png`) })
            }
            await page.keyboard.press('Escape')
            await page.waitForTimeout(150)
          }
        }
        if (tab === 'channels') {
          const edit = page
            .locator('.channel-card')
            .first()
            .getByRole('button', { name: '編集', exact: true })
          if (await edit.count()) {
            await edit.click()
            await page.waitForTimeout(150)
            await page.screenshot({ path: join(output, `${device}-channel-edit-${color}.png`) })
            await page.keyboard.press('Escape')
            await page.waitForTimeout(100)
          }
        }
      }
      await page.close()
    }
  }
  await browser.close()
  browser = await webkit.launch({ headless: true })
  const safariPage = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    locale: 'ja-JP',
    timezoneId: 'Asia/Tokyo',
  })
  await safariPage.route('**/api/chat-assets/**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg/>' }),
  )
  await safariPage.goto(`${context.baseURL}/?tab=videos`, { waitUntil: 'networkidle' })
  const mkvRow = safariPage.locator('.video-row').filter({ hasText: '歌枠 / Singing Stream' })
  if (await mkvRow.count()) {
    await mkvRow.getByRole('button', { name: '再生', exact: true }).click()
    await safariPage.locator('.playback-status').waitFor({ state: 'visible', timeout: 10000 })
    await safariPage.screenshot({ path: join(output, 'webkit-player-preparing.png') })
    await safariPage.waitForFunction(
      () => {
        const video = document.querySelector('.dplayer-mount video')
        return video instanceof HTMLVideoElement && video.currentTime > 0.5
      },
      { timeout: 30000 },
    )
    await safariPage.locator('.playback-status').waitFor({ state: 'hidden', timeout: 10000 })
    await safariPage.screenshot({ path: join(output, 'webkit-player-playing.png') })
    await safariPage.keyboard.press('Escape')
  }
  const av1Row = safariPage
    .locator('.video-row')
    .filter({ hasText: 'AV1 / Opus browser compatibility fixture' })
  await safariPage.getByPlaceholder('タイトル・説明').fill('AV1 / Opus browser compatibility fixture')
  await av1Row.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {})
  if (await av1Row.count()) {
    await av1Row.getByRole('button', { name: '再生', exact: true }).click()
    await safariPage.getByRole('button', { name: '変換して再生' }).waitFor({ timeout: 10000 })
    await safariPage.screenshot({ path: join(output, 'webkit-player-conversion-notice.png') })
    await safariPage.keyboard.press('Escape')
  }
  const brokenRow = safariPage.locator('.video-row').filter({ hasText: '壊れたメディア timeout 案内確認' })
  await safariPage.getByPlaceholder('タイトル・説明').fill('壊れたメディア')
  await brokenRow.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {})
  if (await brokenRow.count()) {
    await brokenRow.getByRole('button', { name: '再生', exact: true }).click()
    await safariPage
      .locator('.playback-status')
      .getByRole('button', { name: '再試行' })
      .waitFor({ timeout: 20000 })
    await safariPage.screenshot({ path: join(output, 'webkit-player-load-failure.png') })
    await safariPage.keyboard.press('Escape')
  }
  await safariPage.close()
  console.log(`Screenshots written to ${output}`)
} finally {
  await browser?.close()
  await stopE2E(context)
}
