// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { test, expect } from '@playwright/test'

test('exports the visible conversation as a text file', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem(
      'reading-room-session',
      JSON.stringify({
        state: {
          sessionId: 'transcript-export-test',
          chatMode: 'discovery',
          messages: [
            {
              id: 'user-message',
              role: 'user',
              content: 'What was the Square Deal?',
              timestamp: '2025-01-01T00:00:00.000Z',
            },
            {
              id: 'assistant-message',
              role: 'assistant',
              content: 'It was Roosevelt’s domestic policy program.',
              timestamp: '2025-01-01T00:00:01.000Z',
            },
          ],
        },
        version: 0,
      })
    )
  })

  await page.goto('/chat')
  await expect(page.getByText('What was the Square Deal?')).toBeVisible()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export Transcript' }).click()
  const download = await downloadPromise

  expect(download.suggestedFilename()).toMatch(/^transcript-\d{4}-\d{2}-\d{2}\.txt$/)

  const stream = await download.createReadStream()
  let contents = ''
  for await (const chunk of stream) {
    contents += chunk.toString()
  }

  expect(contents).toContain('What was the Square Deal?')
  expect(contents).toContain('It was Roosevelt’s domestic policy program.')
})
