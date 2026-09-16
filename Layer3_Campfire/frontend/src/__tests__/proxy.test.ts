// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment node
 *
 * Integration tests for the Next.js middleware (`proxy`) covering rate limiting
 * and request body size enforcement. The token-bucket logic itself is tested in
 * detail in `src/lib/__tests__/rate-limit.test.ts`; here we verify it is wired
 * up correctly for the chat and API routes, with the expected headers.
 */
import { NextRequest } from 'next/server'

import { normalizeExternalImageOrigin, proxy } from '../proxy'
import { __resetForTests } from '@/lib/rate-limit'

function makeRequest(
  pathname: string,
  options: {
    method?: string
    ip?: string
    contentLength?: number | null
    transferEncoding?: string
  } = {}
): NextRequest {
  const { method = 'GET', ip = '203.0.113.1', contentLength, transferEncoding } = options
  const headers: Record<string, string> = { 'x-forwarded-for': ip }
  // The middleware now requires Content-Length on POST/PUT/PATCH. Default to
  // a small honest value so existing tests still pass; pass `null` to omit it.
  const writeMethod = ['POST', 'PUT', 'PATCH'].includes(method.toUpperCase())
  if (contentLength === null) {
    // omit content-length entirely
  } else if (contentLength !== undefined) {
    headers['content-length'] = String(contentLength)
  } else if (writeMethod) {
    headers['content-length'] = '100'
  }
  if (transferEncoding) headers['transfer-encoding'] = transferEncoding
  return new NextRequest(`http://localhost${pathname}`, { method, headers })
}

beforeEach(() => {
  __resetForTests()
})

describe('external image origin configuration', () => {
  test('accepts an HTTPS origin', () => {
    expect(normalizeExternalImageOrigin(' https://images.example ')).toBe('https://images.example')
  })

  test('rejects paths and non-HTTPS origins', () => {
    expect(() => normalizeExternalImageOrigin('https://images.example/path')).toThrow(
      'without credentials or a path'
    )
    expect(() => normalizeExternalImageOrigin('http://images.example')).toThrow(
      'must be an HTTPS origin'
    )
  })
})

describe('proxy rate limiting', () => {
  test('lets /api/chat requests through under the limit and adds headers', () => {
    const res = proxy(makeRequest('/api/chat', { method: 'POST' }))

    expect(res.status).toBe(200)
    expect(res.headers.get('X-RateLimit-Limit')).toBe('5') // CHAT_LIMIT.capacity
    expect(res.headers.get('X-RateLimit-Remaining')).toBe('4')
    expect(res.headers.get('X-RateLimit-Reset')).toBeTruthy()
  })

  test('returns 429 for /api/chat after capacity exceeded', () => {
    // Drain the bucket
    for (let i = 0; i < 5; i++) {
      const res = proxy(makeRequest('/api/chat', { method: 'POST' }))
      expect(res.status).toBe(200)
    }

    const denied = proxy(makeRequest('/api/chat', { method: 'POST' }))

    expect(denied.status).toBe(429)
    expect(denied.headers.get('Retry-After')).toBeTruthy()
    expect(denied.headers.get('X-RateLimit-Limit')).toBe('5')
    expect(denied.headers.get('X-RateLimit-Remaining')).toBe('0')
  })

  test('rate-limits per IP (different IPs use separate buckets)', () => {
    for (let i = 0; i < 5; i++) {
      proxy(makeRequest('/api/chat', { method: 'POST', ip: '1.1.1.1' }))
    }
    expect(proxy(makeRequest('/api/chat', { method: 'POST', ip: '1.1.1.1' })).status).toBe(429)
    // Different IP gets its own bucket
    expect(proxy(makeRequest('/api/chat', { method: 'POST', ip: '2.2.2.2' })).status).toBe(200)
  })

  test('ignores client-injected X-Forwarded-For prefix when extracting client IP', () => {
    // With TRUSTED_PROXY_HOPS=1 (default), only the rightmost XFF entry is trusted.
    // An attacker prepending fake IPs to rotate buckets must not escape rate limiting.
    // All 6 requests below share the same client IP; the 6th should be denied.
    for (let i = 0; i < 5; i++) {
      const res = proxy(
        makeRequest('/api/chat', { method: 'POST', ip: `evil-${i}, 9.9.9.9` })
      )
      expect(res.status).toBe(200)
    }
    const denied = proxy(
      makeRequest('/api/chat', { method: 'POST', ip: 'evil-final, 9.9.9.9' })
    )
    expect(denied.status).toBe(429)
  })

  test('chat and non-chat API endpoints have independent buckets', () => {
    // Drain chat bucket
    for (let i = 0; i < 5; i++) {
      proxy(makeRequest('/api/chat', { method: 'POST' }))
    }
    expect(proxy(makeRequest('/api/chat', { method: 'POST' })).status).toBe(429)
    // /api/artifacts uses API_LIMIT (capacity 20), independent bucket
    expect(proxy(makeRequest('/api/artifacts', { method: 'POST' })).status).toBe(200)
  })

  test('allows /api/health to bypass rate limiting', () => {
    // Drain the chat bucket
    for (let i = 0; i < 5; i++) {
      proxy(makeRequest('/api/chat', { method: 'POST' }))
    }
    // Health probe should always pass
    const res = proxy(makeRequest('/api/health'))
    expect(res.status).toBe(200)
  })

  test('non-API routes pass through without rate limiting', () => {
    // Use a non-API path that the middleware leaves alone
    const res = proxy(makeRequest('/about'))
    expect(res.status).toBe(200)
    expect(res.headers.get('X-RateLimit-Limit')).toBeNull()
  })

  test('applies security headers to every response', () => {
    const res = proxy(makeRequest('/api/chat', { method: 'POST' }))
    expect(res.headers.get('Strict-Transport-Security')).toContain('max-age=')
    expect(res.headers.get('X-Frame-Options')).toBe('DENY')
    expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff')
    expect(res.headers.get('Content-Security-Policy')).toContain("default-src 'self'")
  })

  test('does not emit the deprecated Report-Only CSP header', () => {
    const res = proxy(makeRequest('/api/chat', { method: 'POST' }))
    expect(res.headers.get('Content-Security-Policy-Report-Only')).toBeNull()
  })

  test('omits unsafe-eval from CSP in production', () => {
    const originalEnv = process.env.NODE_ENV
    ;(process.env as Record<string, unknown>).NODE_ENV = 'production'
    jest.resetModules()
    // Re-import so the module-level IS_DEV check re-evaluates.
    const { proxy: prodProxy } = require('../proxy') as typeof import('../proxy')
    try {
      const res = prodProxy(makeRequest('/api/chat', { method: 'POST' }))
      const csp = res.headers.get('Content-Security-Policy') ?? ''
      expect(csp).toContain("script-src 'self' 'unsafe-inline'")
      expect(csp).not.toContain("'unsafe-eval'")
    } finally {
      ;(process.env as Record<string, unknown>).NODE_ENV = originalEnv
      jest.resetModules()
    }
  })
})

describe('proxy body size enforcement', () => {
  test('rejects oversized chat POST with 413', () => {
    const res = proxy(
      makeRequest('/api/chat', { method: 'POST', contentLength: 11 * 1024 })
    )
    expect(res.status).toBe(413)
  })

  test('allows chat POST up to 10 KB', () => {
    const res = proxy(
      makeRequest('/api/chat', { method: 'POST', contentLength: 10 * 1024 })
    )
    expect(res.status).toBe(200)
  })

  test('uses larger default limit (100 KB) for non-chat API routes', () => {
    // 11 KB exceeds chat limit but not default
    const res = proxy(
      makeRequest('/api/artifacts', { method: 'POST', contentLength: 11 * 1024 })
    )
    expect(res.status).toBe(200)

    // 101 KB exceeds default
    __resetForTests() // clear rate limit state
    const tooBig = proxy(
      makeRequest('/api/artifacts', { method: 'POST', contentLength: 101 * 1024 })
    )
    expect(tooBig.status).toBe(413)
  })

  test('does not check body size on GET', () => {
    const res = proxy(
      makeRequest('/api/chat-history/user-1', {
        method: 'GET',
        contentLength: 999_999,
      })
    )
    expect(res.status).toBe(200)
  })

  test('rejects POST with missing Content-Length (was a bypass)', () => {
    const res = proxy(
      makeRequest('/api/chat', { method: 'POST', contentLength: null })
    )
    expect(res.status).toBe(411)
  })

  test('rejects POST with chunked Transfer-Encoding (was a bypass)', () => {
    const res = proxy(
      makeRequest('/api/chat', {
        method: 'POST',
        contentLength: 100,
        transferEncoding: 'chunked',
      })
    )
    expect(res.status).toBe(413)
  })

  test('rejects POST with non-numeric Content-Length', () => {
    // -1 trips the `< 0` check; the parsing helper guards `NaN` similarly.
    const res = proxy(
      makeRequest('/api/chat', { method: 'POST', contentLength: -1 })
    )
    expect(res.status).toBe(400)
  })
})
