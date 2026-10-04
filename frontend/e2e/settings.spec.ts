import { expect, test } from './helpers'
import { go } from './helpers'

test('recording rule dialog supports preview, creation, and active toggle', async ({ page }, testInfo) => {
  await page.goto('/')
  await go(page, '設定')
  await page.getByRole('button', { name: 'ルールを追加' }).click()
  const dialog = page.getByRole('dialog', { name: 'ルールを追加' })
  await dialog.getByLabel('名前', { exact: true }).fill(`E2E ルール ${testInfo.project.name}`)
  await dialog.getByLabel('パターン').fill('歌枠')
  await dialog.getByRole('button', { name: '既存動画でプレビュー' }).click()
  await expect(dialog.locator('.summary-text')).toContainText('count')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  const row = page.locator('.rule-row').filter({ hasText: `E2E ルール ${testInfo.project.name}` })
  await expect(row).toBeVisible()
  await row.getByRole('checkbox').click()
  await expect(page.locator('.toast')).toContainText('ルールを無効にしました')
})
