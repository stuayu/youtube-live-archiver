import { expect, test } from './helpers'
import { go, mainTabs } from './helpers'

test('all screens fit the viewport in light and dark themes', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('response', (response) => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`)
  })
  for (const theme of ['light', 'dark'] as const) {
    await page.addInitScript((value) => localStorage.setItem('yla-theme', value), theme)
    await page.goto('/')
    for (const [tab, label] of mainTabs) {
      await go(page, label)
      if (tab === 'home') await page.waitForURL(/\/$/)
      else await page.waitForURL(new RegExp(`[?&]tab=${tab}(?:&|$)`))
      const dimensions = await page.evaluate(() => ({
        body: document.body.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }))
      expect(dimensions.body, `${testInfo.project.name} ${theme} ${tab}`).toBeLessThanOrEqual(
        dimensions.viewport,
      )
      if (testInfo.project.name === 'mobile') {
        const shortTargets = await page
          .locator('button,a[href],input:not([type="checkbox"]),select,[role="button"]')
          .evaluateAll((elements) =>
            elements
              .filter((element) => {
                if (element.closest('details:not([open])')) return false
                for (let parent: Element | null = element; parent; parent = parent.parentElement) {
                  const style = getComputedStyle(parent)
                  if (style.display === 'none' || style.visibility === 'hidden') return false
                }
                const rect = element.getBoundingClientRect()
                return rect.width > 0 && rect.height > 0 && (rect.width < 44 || rect.height < 44)
              })
              .map((element) => {
                const rect = element.getBoundingClientRect()
                return `${element.tagName}.${element.className}: ${Math.round(rect.width)}x${Math.round(rect.height)}`
              }),
          )
        expect(shortTargets, `${testInfo.project.name} ${theme} ${tab}`).toEqual([])
      }
    }
  }
  expect(errors, errors.join('\n')).toEqual([])
})
