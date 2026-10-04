import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test } from './helpers'
import { go } from './helpers'

test('add, duplicate validation, edit, and reject deleting a referenced storage', async ({
  page,
}, testInfo) => {
  const name = `E2E-${testInfo.project.name}`
  const root = join(tmpdir(), `yla-${testInfo.project.name}-storage`)
  const updatedRoot = join(tmpdir(), `yla-${testInfo.project.name}-storage-updated`)
  await page.goto('/')
  await go(page, 'ストレージ')
  await page.getByRole('button', { name: '保存先を追加' }).click()
  const dialog = page.getByRole('dialog', { name: '保存先を追加' })
  await dialog.getByLabel('保存先名').fill(name)
  await dialog.getByLabel('保存先パス').fill(root)
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  const card = page.locator('.storage-card').filter({ hasText: name })
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: '編集' }).click()
  const editor = page.getByRole('dialog', { name: '保存先を編集' })
  await editor.getByLabel('保存先パス').fill(updatedRoot)
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(card).toContainText(updatedRoot)

  await page.getByRole('button', { name: '保存先を追加' }).click()
  const duplicate = page.getByRole('dialog', { name: '保存先を追加' })
  await duplicate.getByLabel('保存先名').fill(name)
  await duplicate.getByLabel('保存先パス').fill(root)
  await duplicate.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.toast.error')).toContainText('保存先名が重複しています')
  await expect(page.locator('.toast.error')).not.toContainText('{"error"')
  await duplicate.getByRole('button', { name: '閉じる' }).click()

  const referenced = page.locator('.storage-card').filter({ hasText: 'LOCAL' })
  await referenced.locator('details.menu > summary').click()
  await referenced.locator('.menu-items').getByRole('button', { name: '削除' }).click()
  await page.getByRole('dialog', { name: '保存先を削除' }).getByRole('button', { name: '実行' }).click()
  await expect(page.locator('.toast.error').filter({ hasText: '参照中のため削除できません' })).toBeVisible()
  await expect(referenced).toBeVisible()
})
