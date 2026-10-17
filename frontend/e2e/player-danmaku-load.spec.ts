import { mkdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { expect, test } from './helpers'

test('high-volume danmaku stays loaded through two minutes of playback', async ({ page }, testInfo) => {
  test.skip(!['desktop', 'webkit-iphone'].includes(testInfo.project.name))
  test.setTimeout(180_000)
  const calls: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/chat/danmaku/')) calls.push(request.url())
  })
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('高密度コメント配信')
  await page
    .locator('.video-row')
    .filter({ hasText: '高密度コメント配信' })
    .getByRole('button', { name: '再生', exact: true })
    .click()

  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const video = dialog.locator('video')
  await video.evaluate((element) => {
    const target = element as HTMLVideoElement
    target.muted = true
    void target.play()
  })
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime), {
      timeout: 125_000,
      intervals: [500, 1000, 2000],
    })
    .toBeGreaterThan(58)
  const firstFlowCount = await dialog
    .locator('.dplayer-danmaku-item:not(.dplayer-danmaku-item--demo)')
    .count()
  expect(firstFlowCount).toBeGreaterThan(0)

  const shotDir = process.env.YLA_SHOT_DIR
  if (shotDir) {
    await mkdir(resolve(shotDir), { recursive: true })
    await page.screenshot({ path: join(resolve(shotDir), `${testInfo.project.name}-danmaku-60s.png`) })
  }

  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime), {
      timeout: 70_000,
      intervals: [500, 1000, 2000],
    })
    .toBeGreaterThan(118)
  const secondFlowCount = await dialog
    .locator('.dplayer-danmaku-item:not(.dplayer-danmaku-item--demo)')
    .count()
  expect(secondFlowCount).toBeGreaterThan(0)
  if (shotDir) {
    await page.screenshot({ path: join(resolve(shotDir), `${testInfo.project.name}-danmaku-120s.png`) })
  }
  await video.evaluate((element) => {
    const target = element as HTMLVideoElement
    target.pause()
    target.currentTime = 151
    target.dispatchEvent(new Event('timeupdate'))
  })
  await expect.poll(() => calls.length).toBeGreaterThanOrEqual(2)
  expect(calls.length).toBeLessThanOrEqual(3)
  expect(new Set(calls).size).toBe(calls.length)
})
