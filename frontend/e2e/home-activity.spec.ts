import { expect, test } from './helpers'
import { go } from './helpers'

test('home shows attention and links to the related screen; activity is localized', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '要対応' })).toBeVisible()
  await page.getByRole('button', { name: /チャンネルで監視エラー/ }).click()
  await expect(page).toHaveURL(/[?&]tab=channels/)
  await expect(page.getByLabel('監視状態')).toHaveValue('error')
  await expect(page.locator('.channel-card').first()).toContainText('取得に失敗しました')
  const errorDetails = page.locator('.channel-card').first().locator('.inline-error > details')
  await expect(errorDetails).not.toHaveAttribute('open', '')
  await errorDetails.locator('summary').click()
  await expect(page.locator('.channel-card').first()).toContainText('urlopen error')
  await go(page, 'アクティビティ')
  await expect(page.locator('.event-row').first()).toBeVisible()
  await expect(page.locator('.event-row').first()).not.toContainText('MONITOR_ERROR')
})
