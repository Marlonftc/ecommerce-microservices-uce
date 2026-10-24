// UI end-to-end tests: Chromium against the whole system in Docker, always through
// the gateway (the only published port). One worker, in file order: the tests share
// the stock of the system and some of them stop microservices.
import { defineConfig, devices } from '@playwright/test';
import { GATEWAY_URL } from './support/env';

export default defineConfig({
  testDir: './specs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  // Asynchronous work of the backend: worker every 2 s (compose.e2e.yaml), slow payment
  // 8 s, gateway timeout 30 s. Each test sets a longer limit when it needs one.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  globalSetup: './global-setup.ts',
  globalTeardown: './global-teardown.ts',
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: GATEWAY_URL,
    locale: 'es-EC',
    timezoneId: 'America/Guayaquil',
    // Screenshots only when a test fails. Traces are off by default because they record
    // request headers (test tokens); UI_TRACE=1 turns them on for local debugging.
    screenshot: 'only-on-failure',
    trace: process.env.UI_TRACE === '1' ? 'retain-on-failure' : 'off',
    video: 'off',
    launchOptions: process.env.UI_CHROMIUM_PATH ? { executablePath: process.env.UI_CHROMIUM_PATH } : {},
  },
});
