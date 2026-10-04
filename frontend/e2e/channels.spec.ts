import { expect, test } from './helpers'
import { go } from './helpers'

test('preview, register, and edit retention to permanent', async ({ page }, testInfo) => {
  await page.goto('/')
  await go(page, 'チャンネル')
  await page.getByRole('button', { name: 'チャンネルを追加' }).click()
  const dialog = page.getByRole('dialog', { name: 'チャンネルを追加' })
  await expect(dialog).toHaveAttribute('aria-modal', 'true')
  await dialog.getByLabel('チャンネル URL / @handle / ID').fill(`@e2e-${testInfo.project.name}`)
  await dialog.getByRole('button', { name: 'プレビュー' }).click()
  await expect(dialog.locator('.preview-panel')).toContainText('E2E チャンネル')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  const createdName = `E2E チャンネル ${testInfo.project.name}`
  const card = page.locator('.channel-card').filter({ hasText: createdName })
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: '編集' }).click()
  const editor = page.getByRole('dialog', { name: 'チャンネル設定' })
  await expect(editor.getByLabel('チャンネル URL / @handle / ID')).toHaveCount(0)
  await expect(editor.getByText(createdName)).toBeVisible()
  const fromStart = editor.getByLabel('配信の先頭から録画する')
  await expect(fromStart).toBeChecked()
  await fromStart.uncheck()
  await editor.getByLabel('保存期間', { exact: true }).selectOption('permanent')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(card).toContainText('永久')
  await page.reload()
  await go(page, 'チャンネル')
  await expect(page.locator('.channel-card').filter({ hasText: createdName })).toContainText('永久')
  await page
    .locator('.channel-card')
    .filter({ hasText: createdName })
    .getByRole('button', { name: '編集' })
    .click()
  await expect(
    page.getByRole('dialog', { name: 'チャンネル設定' }).getByLabel('配信の先頭から録画する'),
  ).not.toBeChecked()
})

test('422 field errors appear under their input without raw JSON', async ({ page }, testInfo) => {
  await page.route(/\/api\/channels$/, async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({
          error: '入力内容を確認してください',
          fields: { url: '形式を確認してください' },
        }),
      })
    } else await route.continue()
  })
  await page.goto('/?tab=channels')
  await page.getByRole('button', { name: 'チャンネルを追加' }).click()
  const dialog = page.getByRole('dialog', { name: 'チャンネルを追加' })
  await dialog.getByLabel('チャンネル URL / @handle / ID').fill(`@e2e-${testInfo.project.name}`)
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog.locator('#channel-url').locator('..').locator('..')).toContainText(
    '形式を確認してください',
  )
  await expect(dialog.getByText('{"error"', { exact: false })).toHaveCount(0)
})
