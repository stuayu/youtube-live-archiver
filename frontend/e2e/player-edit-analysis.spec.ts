import { expect, test } from './helpers'

test('edit locations are listed, marked on the seek bar, and skip buttons move playback', async ({
  page,
}) => {
  await page.goto('/?tab=videos')
  await page.getByPlaceholder('タイトル・説明').fill('高密度コメント配信')
  await page
    .locator('.video-row')
    .filter({ hasText: '高密度コメント配信' })
    .getByRole('button', { name: '再生', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: /再生:/ })
  const video = dialog.locator('video')
  await expect(video).toBeVisible({ timeout: 15000 })
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).duration))
    .toBeGreaterThan(0)

  const removed = dialog.locator('.yla-edit-marker-removed')
  await expect(removed).toHaveCount(1)
  const left = await removed.evaluate((element) => (element as HTMLElement).style.left)
  expect(Number.parseFloat(left)).toBeCloseTo((30 / 180) * 100, 0)
  await expect(dialog.locator('.yla-edit-marker-added')).toHaveCount(1)

  await dialog.getByRole('button', { name: 'タイムライン', exact: true }).click()
  await dialog.getByRole('tab', { name: '編集箇所' }).click()
  await expect(dialog.getByText('YouTube 版でカットされた区間 1 件')).toBeVisible()
  await dialog.locator('.edit-region-removed .edit-region-seek').click()
  await expect
    .poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThanOrEqual(29.5)

  const position = () => video.evaluate((element) => (element as HTMLVideoElement).currentTime)
  await video.evaluate((element) => (element as HTMLVideoElement).pause())
  const before = await position()
  await dialog.getByRole('button', { name: '10秒進む' }).click()
  await expect.poll(position).toBeGreaterThanOrEqual(before + 9.5)
  await dialog.getByRole('button', { name: '10秒戻る' }).click()
  await expect.poll(position).toBeLessThan(before + 1)
})
