import { spawnSync } from 'node:child_process'
import { resolve, join } from 'node:path'
import { expect, test as base, type Page } from '@playwright/test'

interface SeedEnvironment {
  DATABASE_URL: string
  STORAGE_ROOT: string
  DATA_DIR: string
  YT_DLP_PATH: string
  FAKE_YTDLP_STATE: string
  PYTHONPATH: string
  YLA_E2E_ADMIN_URL: string
  YLA_E2E_DATABASE_NAME: string
}

function seedEnvironment(): SeedEnvironment | null {
  const raw = process.env.YLA_E2E_SEED_ENV
  if (!raw) return null
  const parsed: unknown = JSON.parse(raw)
  if (typeof parsed !== 'object' || parsed === null) return null
  const values = Object.fromEntries(
    Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
  )
  const required = [
    'DATABASE_URL',
    'STORAGE_ROOT',
    'DATA_DIR',
    'YT_DLP_PATH',
    'FAKE_YTDLP_STATE',
    'PYTHONPATH',
    'YLA_E2E_ADMIN_URL',
    'YLA_E2E_DATABASE_NAME',
  ]
  if (required.some((key) => !values[key])) return null
  return values as SeedEnvironment
}

function resetSeed() {
  const env = seedEnvironment()
  if (!env) return
  const backend = resolve(process.cwd(), '..', 'backend')
  const python =
    process.env.YLA_PYTHON ||
    join(
      backend,
      '.venv',
      process.platform === 'win32' ? 'Scripts' : 'bin',
      process.platform === 'win32' ? 'python.exe' : 'python',
    )
  const databaseTool = join(backend, 'tests', 'e2e_database.py')
  const cleanup = spawnSync(
    python,
    [databaseTool, 'close-sessions', env.YLA_E2E_ADMIN_URL, env.YLA_E2E_DATABASE_NAME],
    {
      cwd: backend,
      env: { ...process.env, ...env },
      stdio: 'inherit',
    },
  )
  if (cleanup.status !== 0)
    throw new Error(`E2E DB session cleanup failed: ${cleanup.error?.message || cleanup.status}`)
  const result = spawnSync(python, [join(backend, 'tests', 'e2e_seed.py')], {
    cwd: backend,
    env: { ...process.env, ...env },
    stdio: 'inherit',
  })
  if (result.status !== 0) throw new Error(`E2E seed reset failed: ${result.error?.message || result.status}`)
}

export { expect }
export const test = base.extend<{ isolatedSeed: void }>({
  isolatedSeed: [
    async ({ page }, use) => {
      void page
      resetSeed()
      await page.route('**/api/chat-assets/**', (route) =>
        route.fulfill({
          status: 200,
          contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#00aacc"/></svg>',
        }),
      )
      await use()
    },
    { auto: true },
  ],
})

export const mainTabs: Array<[string, string]> = [
  ['home', 'ホーム'],
  ['channels', 'チャンネル'],
  ['upcoming', '配信予定'],
  ['videos', 'アーカイブ'],
  ['search', '全文検索'],
  ['storages', 'ストレージ'],
  ['analysis', '分析'],
  ['activity', 'アクティビティ'],
  ['settings', '設定'],
]

export async function go(page: Page, label: string) {
  if ((page.viewportSize()?.width ?? 1440) < 1024) {
    if (!['ホーム', 'チャンネル', '配信予定', 'アーカイブ'].includes(label)) {
      await page.locator('.mobile-tab-more .mobile-tab').click()
      await page.locator('.mobile-more-menu').getByRole('button', { name: label, exact: true }).click()
    } else await page.locator('.mobile-nav .mobile-tab').filter({ hasText: label }).click()
  } else await page.locator('.sidebar .nav-item').filter({ hasText: label }).click()
}

export async function visitEveryTab(page: Page) {
  for (const [tab, label] of mainTabs) {
    await go(page, label)
    await page.locator('main').waitFor({ state: 'visible' })
    if (tab === 'home') await page.waitForURL(/\/$/)
    else await page.waitForURL(new RegExp(`[?&]tab=${tab}(?:&|$)`))
  }
}
