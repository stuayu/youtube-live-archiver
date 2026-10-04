import { expect, test } from './helpers'
import { go } from './helpers'

test('upcoming events group by date and schedule can be toggled', async ({ page }) => {
  await page.goto('/')
  await go(page, '配信予定')
  await expect(page.getByRole('heading', { name: '明日', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '時刻未定', exact: true })).toBeVisible()
  await expect(page.getByText(/開始待ち（予定を \d+ 分超過）/)).toBeVisible()
  const row = page.locator('.video-row').filter({ hasText: '録画しない設定の配信' })
  const schedule = row.getByRole('checkbox')
  if (!(await schedule.isChecked())) await schedule.click()
  await expect(row).toContainText('録画予約')
  const menu = row.locator('details.menu')
  await menu.locator('summary').click()
  await expect(menu.getByRole('link', { name: /YouTube で開く/ })).toHaveAttribute(
    'href',
    /youtube\.com\/watch/,
  )
})
