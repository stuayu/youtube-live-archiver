import { defineConfig, devices } from '@playwright/test'
const external = process.env.YLA_E2E_URL
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  globalSetup: './global-setup.ts',
  use: {
    baseURL: external || 'http://127.0.0.1:4173',
    locale: 'ja-JP',
    timezoneId: 'Asia/Tokyo',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { args: ['--lang=ja-JP'] },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } },
    { name: 'tablet', use: { ...devices['Desktop Chrome'], viewport: { width: 820, height: 1120 } } },
    {
      name: 'mobile',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
})
