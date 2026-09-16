// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment node
 */
import { NextRequest } from 'next/server'

jest.mock('@/lib/env', () => ({
  env: {
    PYTHON_RAG_API_URL: 'http://backend.test',
    RAG_API_KEY: 'test-key',
    AZURE_STORAGE_ACCOUNT: 'examplestorage',
  },
}))

jest.mock('@/lib/logger', () => ({
  logger: {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  },
}))

import { GET } from '../route'

function makeRequest(url: string, headers: Record<string, string> = {}): NextRequest {
  return new NextRequest(url, { method: 'GET', headers })
}

function makeUpstreamResponse(
  init: {
    status?: number
    body?: ReadableStream | string | null
    headers?: Record<string, string>
  } = {},
): Response {
  const headers = new Headers(init.headers ?? {})
  const status = init.status ?? 200
  const body =
    typeof init.body === 'string'
      ? new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(init.body as string))
            controller.close()
          },
        })
      : (init.body ?? null)
  return {
    ok: status >= 200 && status < 300,
    status,
    headers,
    body,
  } as unknown as Response
}

beforeEach(() => {
  jest.clearAllMocks()
  global.fetch = jest.fn() as unknown as typeof fetch
})

describe('GET /api/artifacts/pdf', () => {
  test('returns 400 when url parameter is missing', async () => {
    const res = await GET(makeRequest('http://localhost/api/artifacts/pdf'))
    expect(res.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('returns 400 when url is not parseable', async () => {
    const res = await GET(
      makeRequest('http://localhost/api/artifacts/pdf?url=' + encodeURIComponent('not a url')),
    )
    expect(res.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects other Azure storage accounts (SSRF guard)', async () => {
    const hostile = encodeURIComponent(
      'https://attacker.blob.core.windows.net/public/huge.bin',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/pdf?url=${hostile}`))
    expect(res.status).toBe(403)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects subdomain trick like examplestorage.blob.core.windows.net.evil.tld', async () => {
    const hostile = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net.evil.tld/x.pdf',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/pdf?url=${hostile}`))
    expect(res.status).toBe(403)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects non-https schemes', async () => {
    const hostile = encodeURIComponent(
      'http://examplestorage.blob.core.windows.net/x.pdf',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/pdf?url=${hostile}`))
    expect(res.status).toBe(403)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects oversize responses via Content-Length before streaming', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      makeUpstreamResponse({
        status: 200,
        body: 'pdf-bytes',
        headers: { 'content-length': String(60 * 1024 * 1024) },
      }),
    )

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.pdf',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/pdf?url=${allowed}`))
    expect(res.status).toBe(413)
  })

  test('streams the upstream body without buffering', async () => {
    const upstream = makeUpstreamResponse({
      status: 200,
      body: 'pdf-bytes',
      headers: { 'content-length': '9', 'accept-ranges': 'bytes' },
    })
    ;(global.fetch as jest.Mock).mockResolvedValue(upstream)

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.pdf',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/pdf?url=${allowed}`))
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toBe('application/pdf')
    expect(res.headers.get('content-length')).toBe('9')
    expect(res.headers.get('accept-ranges')).toBe('bytes')
    expect(res.headers.get('cache-control')).toContain('private')
    // The body of the NextResponse should be the same stream we returned from
    // upstream — we never call arrayBuffer() on it server-side.
    const body = await res.text()
    expect(body).toBe('pdf-bytes')
  })

  test('forwards Range header and propagates 206 + Content-Range', async () => {
    const upstream = makeUpstreamResponse({
      status: 206,
      body: 'chunk',
      headers: {
        'content-length': '5',
        'content-range': 'bytes 0-4/100',
        'accept-ranges': 'bytes',
      },
    })
    ;(global.fetch as jest.Mock).mockResolvedValue(upstream)

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.pdf',
    )
    const res = await GET(
      makeRequest(`http://localhost/api/artifacts/pdf?url=${allowed}`, {
        Range: 'bytes=0-4',
      }),
    )

    expect(global.fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ headers: expect.objectContaining({ Range: 'bytes=0-4' }) }),
    )
    expect(res.status).toBe(206)
    expect(res.headers.get('content-range')).toBe('bytes 0-4/100')
  })

  test('forwards upstream non-OK status with error payload', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      makeUpstreamResponse({ status: 404, body: null }),
    )

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/missing.pdf',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/pdf?url=${allowed}`))
    expect(res.status).toBe(404)
  })

  test('returns 500 when upstream fetch rejects', async () => {
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network down'))
    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.pdf',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/pdf?url=${allowed}`))
    expect(res.status).toBe(500)
  })
})

describe('GET /api/artifacts/pdf without AZURE_STORAGE_ACCOUNT', () => {
  beforeEach(() => {
    jest.resetModules()
    jest.doMock('@/lib/env', () => ({
      env: {
        PYTHON_RAG_API_URL: 'http://backend.test',
        RAG_API_KEY: 'test-key',
        AZURE_STORAGE_ACCOUNT: undefined,
      },
    }))
    jest.doMock('@/lib/logger', () => ({
      logger: { info: jest.fn(), warn: jest.fn(), error: jest.fn() },
    }))
  })

  test('returns 503 when proxy is not configured', async () => {
    const { GET: getUnconfigured } = await import('../route')
    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.pdf',
    )
    const res = await getUnconfigured(
      makeRequest(`http://localhost/api/artifacts/pdf?url=${allowed}`),
    )
    expect(res.status).toBe(503)
  })
})
