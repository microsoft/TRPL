// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { defineConfig, devices } from '@playwright/test'

const port = Number(process.env.PLAYWRIGHT_PORT ?? '3000')
const browserChannel = process.env.PLAYWRIGHT_BROWSER_CHANNEL
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER
  ? process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === 'true'
  : !process.env.CI

/**
 * Playwright config for end-to-end smoke tests.
 *
 * These tests boot the Next.js dev server (with placeholder env vars so the
 * env-schema check passes) and exercise the most critical UI flows. The
 * backend is mocked at the network layer via `page.route()` — these are
 * frontend-only smoke tests.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  timeout: 30_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], channel: browserChannel },
    },
  ],

  webServer: {
    command: `bun run dev --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    timeout: 120_000,
    reuseExistingServer,
    env: {
      PYTHON_RAG_API_URL: 'http://placeholder.test',
      RAG_API_KEY: 'placeholder-key',
      NEXT_PUBLIC_APP_NAME: 'Reading Room (e2e)',
    },
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
