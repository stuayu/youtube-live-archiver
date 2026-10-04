import { chromium } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { startE2E, stopE2E } from './e2e-env.mjs'
const context = await startE2E()
let browser
const output = resolve('shots', process.argv[2] === 'review' ? 'review' : 'latest')
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
            if (width === 1440 || width === 390) {
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
  console.log(`Screenshots written to ${output}`)
} finally {
  await browser?.close()
  await stopE2E(context)
}
