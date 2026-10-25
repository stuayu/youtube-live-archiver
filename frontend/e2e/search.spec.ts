import { expect, test } from './helpers'
import { go } from './helpers'

test('transcript search groups matches and opens playback at the hit time', async ({ page }) => {
  await page.goto('/')
  await go(page, '全文検索')
  await page.getByPlaceholder('文字起こし内のキーワード').fill('雑談')
  await page.getByRole('button', { name: '検索', exact: true }).click()
  const result = page.locator('.card').filter({ hasText: '朝までまったりお話しする枠' }).nth(1)
  await expect(result).toBeVisible()
  await result.getByRole('button', { name: 'この位置から再生' }).click()
  const player = page.getByRole('dialog', { name: /再生:/ })
  const video = player.locator('video')
  await expect(video).toBeVisible({ timeout: 15000 })
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThanOrEqual(5)
  await page.keyboard.press('Escape')
})
