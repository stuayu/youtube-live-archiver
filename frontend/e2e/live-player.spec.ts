import { expect, test } from './helpers'

test('recording HLS uses DPlayer native live control and settings menu', async ({ page }) => {
  await page.goto('/?tab=home')
  const recording = page.locator('.card').filter({ hasText: '録画中の配信 LIVE NOW' })
  await recording.getByRole('button', { name: '視聴', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const liveBadge = dialog.locator('.dplayer-icons-left .dplayer-live-badge')
  await expect(liveBadge).toBeVisible()
  await expect(liveBadge).toContainText('ライブ')
  await liveBadge.click()
  await dialog.locator('.dplayer-setting-icon').click()
  await expect(dialog.locator('.dplayer-setting-box .yla-setting-live-follow')).toBeVisible()
  await expect(dialog.locator('.dplayer-setting-box .dplayer-setting-speed')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeVisible()
})
