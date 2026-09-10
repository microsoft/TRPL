import { test, expect } from '@playwright/test'

/**
 * Smoke tests against the /api/chat route from inside the running app.
 * Validates contract-level behavior (400 on bad input, 429 from rate limit
 * proxy) without needing the Python backend.
 */
test.describe('/api/chat contract', () => {
  test('returns 400 for empty message', async ({ request }) => {
    const res = await request.post('/api/chat', {
      data: { message: '', sessionId: 'e2e-test' },
    })
    expect(res.status()).toBe(400)
    const body = await res.json()
    expect(body.error).toBe('Invalid request')
  })

  test('returns 400 when sessionId is missing', async ({ request }) => {
    const res = await request.post('/api/chat', {
      data: { message: 'hi' },
    })
    expect(res.status()).toBe(400)
  })

  test('rate-limits after burst (proxy returns 429)', async ({ request }) => {
    // The proxy middleware allows 5 chat POSTs per IP before throttling.
    // Burst 6 with valid payloads and expect at least one 429.
    const statuses: number[] = []
    for (let i = 0; i < 7; i++) {
      const res = await request.post('/api/chat', {
        data: { message: 'hello', sessionId: `e2e-burst-${i}` },
      })
      statuses.push(res.status())
    }
    expect(statuses.some((s) => s === 429)).toBe(true)
  })
})

test.describe('/api/health', () => {
  test('liveness endpoint returns 200', async ({ request }) => {
    const res = await request.get('/api/health')
    // Either 200 (real) or 503 (degraded) — both indicate the proxy passes through
    expect([200, 503]).toContain(res.status())
  })
})
