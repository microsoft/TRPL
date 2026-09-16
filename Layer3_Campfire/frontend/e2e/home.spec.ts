// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { test, expect } from '@playwright/test'

test.describe('Home page', () => {
  test('renders title and tagline', async ({ page }) => {
    await page.goto('/')

    await expect(page.getByRole('heading', { level: 1, name: 'Theodore Roosevelt' })).toBeVisible()
    await expect(page.getByText(/Discover the Life/i)).toBeVisible()
  })

  test('shows both prompt inputs and a submit button', async ({ page }) => {
    await page.goto('/')

    // Wait for the SPA to render and gallery assets to settle.
    const actionInput = page.getByLabel('Select an action')
    await expect(actionInput).toBeVisible({ timeout: 15_000 })

    await expect(page.getByLabel('Enter a topic')).toBeVisible()
    // Either submit-when-text or voice-when-empty fulfills this; both share
    // the role=button on the right-hand side of the SearchBar.
    await expect(
      page.getByRole('button', { name: /Submit|Voice input/i }).first()
    ).toBeVisible()
  })

  test('navigates to /about from the home footer', async ({ page }) => {
    await page.goto('/')

    await page.getByRole('button', { name: /How we use Artificial Intelligence/i }).click()
    await page.waitForURL(/\/about/, { timeout: 10_000 })
    await expect(page).toHaveURL(/\/about/)
  })
})
