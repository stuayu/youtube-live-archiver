import { expect, test } from './helpers'
import { go } from './helpers'

test('manually add upcoming and live/archive videos; validate errors and Escape', async ({ page }) => {
  const externalImageRequests: string[] = []
  page.on('request', (request) => {
    if (/youtube\.com|ytimg\.com/.test(new URL(request.url()).hostname)) {
      externalImageRequests.push(request.url())
    }
  })
  await page.goto('/?tab=upcoming')
  await go(page, '配信予定')
  await page.getByRole('button', { name: 'URL から追加' }).click()
  const dialog = page.getByRole('dialog', { name: 'URL から動画を追加' })
  await expect(dialog).toBeVisible()
  const box = await dialog.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual((await page.evaluate(() => innerWidth)) + 1)
  expect(box!.y + box!.height).toBeLessThanOrEqual((await page.evaluate(() => innerHeight)) + 1)
  const content = dialog.locator('.dialog-body')
  const padding = await content.evaluate((element) => {
    const style = getComputedStyle(element)
    return [Number.parseFloat(style.paddingLeft), Number.parseFloat(style.paddingTop)]
  })
  expect(padding[0]).toBeGreaterThanOrEqual(12)
  expect(padding[1]).toBeGreaterThanOrEqual(12)
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()

  await page.getByRole('button', { name: 'URL から追加' }).click()
  await dialog.getByLabel('YouTube 動画 URL または動画 ID').fill('invalid')
  await dialog.getByRole('button', { name: '確認' }).click()
  await expect(dialog.locator('.inline-error')).toContainText('11 文字')

  await dialog.getByLabel('YouTube 動画 URL または動画 ID').fill('https://youtu.be/manup000001')
  await dialog.getByRole('button', { name: '確認' }).click()
  await expect(dialog).toContainText('配信予定（')
  await expect(dialog).toContainText('監視しないチャンネルとして登録します')
  await dialog.getByRole('button', { name: '追加', exact: true }).click()
  await expect(page).toHaveURL(/tab=upcoming/)
  await expect(page.getByText('配信予定に追加しました')).toBeVisible()

  await go(page, '配信予定')
  await page.getByRole('button', { name: 'URL から追加' }).click()
  await dialog
    .getByLabel('YouTube 動画 URL または動画 ID')
    .fill('https://www.youtube.com/watch?v=manliv00001')
  await dialog.getByRole('button', { name: '確認' }).click()
  await expect(dialog).toContainText('E2E manual live')
  await dialog.getByRole('button', { name: '追加', exact: true }).click()
  await expect(page.getByText('配信予定に追加しました')).toBeVisible()
  await expect(page.locator('.video-row').filter({ hasText: 'E2E manual live' })).toContainText('手動追加')

  await go(page, 'アーカイブ')
  await page.getByRole('button', { name: 'URL から追加' }).click()
  await dialog
    .getByLabel('YouTube 動画 URL または動画 ID')
    .fill('https://www.youtube.com/watch?v=manarc00001')
  await dialog.getByRole('button', { name: '確認' }).click()
  await expect(dialog).toContainText('E2E manual archive')
  await dialog.getByRole('button', { name: '追加', exact: true }).click()
  await expect(page.locator('.video-row').filter({ hasText: 'E2E manual archive' })).toContainText('手動追加')
  expect(externalImageRequests).toEqual([])
})
