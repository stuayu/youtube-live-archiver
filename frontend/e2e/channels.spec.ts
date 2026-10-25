import { expect, test } from './helpers'
import { go } from './helpers'

test('preview, register, and edit retention to permanent', async ({ page }, testInfo) => {
  await page.goto('/')
  await go(page, 'チャンネル')
  await page.getByRole('button', { name: 'チャンネルを追加' }).click()
  const dialog = page.getByRole('dialog', { name: 'チャンネルを追加' })
  await expect(dialog).toHaveAttribute('aria-modal', 'true')
  await expect(dialog.getByLabel('監視間隔')).toHaveValue('AUTO')
  await dialog.getByLabel('監視間隔').selectOption('360')
  await dialog.getByLabel('チャンネル URL / @handle / ID').fill(`@e2e-${testInfo.project.name}`)
  await dialog.getByRole('button', { name: 'プレビュー' }).click()
  await expect(dialog.locator('.preview-panel')).toContainText('E2E チャンネル')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  const createdName = `E2E チャンネル ${testInfo.project.name}`
  const card = page.locator('.channel-card').filter({ hasText: createdName })
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: '編集' }).click()
  const editor = page.getByRole('dialog', { name: 'チャンネル設定' })
  await expect(editor.getByLabel('監視間隔')).toHaveValue('360')
  const profileResponse = page.waitForResponse((response) => response.url().includes('/activity-profile'))
  await editor.getByLabel('監視間隔').selectOption('AUTO')
  expect((await profileResponse).ok()).toBe(true)
  await expect(editor.getByText(/配信の履歴が少ないため/)).toBeVisible()
  await expect(editor.locator('.activity-hours > span')).toHaveCount(24)
  await expect
    .poll(() =>
      editor.locator('.activity-hours').evaluate((element) => element.scrollWidth <= element.clientWidth),
    )
    .toBe(true)
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

test('bulk switch fixed monitoring intervals to automatic with confirmation', async ({ page }) => {
  await page.goto('/?tab=channels')
  const bulk = page.getByRole('button', { name: /固定間隔のチャンネルをすべて自動にする/ })
  await expect(bulk).toBeVisible()
  await bulk.click()
  const confirmation = page.getByRole('dialog', { name: '監視間隔を自動に変更' })
  await expect(confirmation).toContainText(/チャンネルの固定間隔/)
  await confirmation.getByRole('button', { name: '実行' }).click()
  await expect(page.getByRole('button', { name: /固定間隔のチャンネルをすべて自動にする/ })).toHaveCount(0)
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
