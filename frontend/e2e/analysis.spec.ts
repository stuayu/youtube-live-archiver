import { expect, test } from './helpers'

test('analysis renders seeded trend with explicit gaps', async ({ page }) => {
  await page.goto('/?tab=analysis')
  await page.getByRole('combobox').first().selectOption({ label: 'NASA' })
  const chart = page.getByRole('img', { name: 'チャンネル統計推移' })
  await expect(chart).toBeVisible()
  await expect(chart.locator('circle')).toHaveCount(37)
  await expect(chart.locator('path')).toHaveCount(3)
  await expect(chart.locator('title').first()).toContainText('人')
  await expect(page.locator('.chart .meta')).toContainText('/')
})
