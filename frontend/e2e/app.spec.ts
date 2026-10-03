import { expect, test } from '@playwright/test'

const tabs: Array<[string, string]> = [
  ['channels', 'チャンネル'], ['upcoming', '配信予定'], ['videos', '動画アーカイブ'],
  ['search', '全文検索'], ['storages', 'ストレージ'], ['analysis', '分析'], ['settings', '設定'],
]

test('tabs, responsive layout, query state, forms, and touch targets', async ({ page }, testInfo) => {
  const jsErrors: string[] = []
  const resourceErrors: string[] = []
  page.on('pageerror', error => jsErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') jsErrors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) resourceErrors.push(`${response.status()} ${response.url()}`) })
  await page.goto('/')

  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme })
    for (const [key, label] of tabs) {
      await page.getByRole('navigation').getByRole('button', { name: label }).click()
      await expect(page).toHaveURL(new RegExp(`[?&]tab=${key}(?:&|$)`))
      await expect(page.locator('main')).toBeVisible()
      const widths = await page.evaluate(() => ({ body: document.body.scrollWidth, root: document.documentElement.clientWidth }))
      expect(widths.body, `${testInfo.project.name} ${colorScheme} ${key} horizontal overflow`).toBeLessThanOrEqual(widths.root)
    }
  }

  await page.getByRole('navigation').getByRole('button', { name: 'チャンネル' }).click()
  const url = page.getByPlaceholder('https://youtube.com/@example または @example / UC…')
  await expect(url).toBeVisible()
  await url.fill('https://www.youtube.com/@e2e-check')
  await expect(url).toHaveValue('https://www.youtube.com/@e2e-check')

  await page.getByRole('navigation').getByRole('button', { name: '動画アーカイブ' }).click()
  const keyword = page.getByPlaceholder('タイトル・説明')
  await keyword.fill('e2e filter')
  await expect(page).toHaveURL(/[?&]tab=videos/)
  await page.reload()
  await expect(page.getByRole('navigation').getByRole('button', { name: '動画アーカイブ' })).toHaveClass(/active/)
  await expect(page.getByPlaceholder('タイトル・説明')).toHaveValue('e2e filter')

  const undersized = await page.locator('nav button, button.primary, button.plain, button.refresh, input, select').evaluateAll(nodes =>
    nodes.filter(node => {
      const rect = node.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0 && (rect.width < 44 || rect.height < 44)
    }).map(node => ({ text: (node as HTMLElement).innerText, tag: node.tagName, rect: node.getBoundingClientRect().toJSON() })),
  )
  expect(undersized, JSON.stringify(undersized)).toEqual([])
  expect(jsErrors, jsErrors.join('\n')).toEqual([])
  expect(resourceErrors, resourceErrors.join('\n')).toEqual([])
})
