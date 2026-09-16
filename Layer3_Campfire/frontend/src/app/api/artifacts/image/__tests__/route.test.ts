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

jest.mock('sharp', () => {
  const instance = {
    webp: jest.fn().mockReturnThis(),
    toBuffer: jest.fn().mockResolvedValue(Buffer.from('webp-bytes')),
  }
  return jest.fn().mockReturnValue(instance)
})

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
  const bodyString = typeof init.body === 'string' ? init.body : ''
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
    arrayBuffer: () =>
      Promise.resolve(new TextEncoder().encode(bodyString).buffer as ArrayBuffer),
  } as unknown as Response
}

beforeEach(() => {
  jest.clearAllMocks()
  global.fetch = jest.fn() as unknown as typeof fetch
})

describe('GET /api/artifacts/image', () => {
  test('returns 400 when url parameter is missing', async () => {
    const res = await GET(makeRequest('http://localhost/api/artifacts/image'))
    expect(res.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('returns 400 when url is not parseable', async () => {
    const res = await GET(
      makeRequest('http://localhost/api/artifacts/image?url=' + encodeURIComponent('not a url')),
    )
    expect(res.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects other Azure storage accounts (SSRF guard)', async () => {
    const hostile = encodeURIComponent(
      'https://attacker.blob.core.windows.net/public/huge.bin',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${hostile}`))
    expect(res.status).toBe(403)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects subdomain trick like examplestorage.blob.core.windows.net.evil.tld', async () => {
    const hostile = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net.evil.tld/x.jpg',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${hostile}`))
    expect(res.status).toBe(403)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects non-https schemes', async () => {
    const hostile = encodeURIComponent(
      'http://examplestorage.blob.core.windows.net/x.jpg',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${hostile}`))
    expect(res.status).toBe(403)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('rejects oversize responses via Content-Length before streaming', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      makeUpstreamResponse({
        status: 200,
        body: 'image-bytes',
        headers: { 'content-length': String(30 * 1024 * 1024) },
      }),
    )

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.jpg',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
    expect(res.status).toBe(413)
  })

  test('streams the upstream body and preserves the upstream Content-Type', async () => {
    const upstream = makeUpstreamResponse({
      status: 200,
      body: 'image-bytes',
      headers: {
        'content-length': '11',
        'content-type': 'image/jpeg',
        'accept-ranges': 'bytes',
      },
    })
    ;(global.fetch as jest.Mock).mockResolvedValue(upstream)

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.jpg',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
    expect(res.status).toBe(200)
    // Unlike the PDF proxy, Content-Type is passed through, not hardcoded.
    expect(res.headers.get('content-type')).toBe('image/jpeg')
    expect(res.headers.get('content-length')).toBe('11')
    expect(res.headers.get('accept-ranges')).toBe('bytes')
    expect(res.headers.get('cache-control')).toContain('private')
    const body = await res.text()
    expect(body).toBe('image-bytes')
  })

  test('falls back to octet-stream when upstream omits Content-Type', async () => {
    const upstream = makeUpstreamResponse({
      status: 200,
      body: 'tiff-bytes',
      headers: { 'content-length': '10' },
    })
    ;(global.fetch as jest.Mock).mockResolvedValue(upstream)

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.png',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toBe('application/octet-stream')
  })

  test('forwards Range header and propagates 206 + Content-Range', async () => {
    const upstream = makeUpstreamResponse({
      status: 206,
      body: 'chunk',
      headers: {
        'content-length': '5',
        'content-type': 'image/png',
        'content-range': 'bytes 0-4/100',
        'accept-ranges': 'bytes',
      },
    })
    ;(global.fetch as jest.Mock).mockResolvedValue(upstream)

    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.png',
    )
    const res = await GET(
      makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`, {
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
      'https://examplestorage.blob.core.windows.net/content-assets/missing.jpg',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
    expect(res.status).toBe(404)
  })

  test('returns 500 when upstream fetch rejects', async () => {
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network down'))
    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.jpg',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
    expect(res.status).toBe(500)
  })

  test('transcodes TIFF to WebP when URL has .tif or .tiff extension', async () => {
    for (const ext of ['.tif', '.tiff']) {
      ;(global.fetch as jest.Mock).mockResolvedValue(
        makeUpstreamResponse({ status: 200, body: 'tiff-bytes', headers: {} }),
      )
      const allowed = encodeURIComponent(
        `https://examplestorage.blob.core.windows.net/content-assets/scan${ext}`,
      )
      const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
      expect(res.status).toBe(200)
      expect(res.headers.get('content-type')).toBe('image/webp')
    }
  })

  test('transcodes TIFF to WebP when content-type is image/tiff', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      makeUpstreamResponse({
        status: 200,
        body: 'tiff-bytes',
        headers: { 'content-type': 'image/tiff' },
      }),
    )
    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/scan.jpg',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toBe('image/webp')
  })

  test('returns 502 when TIFF transcoding fails', async () => {
    const sharpMock = jest.requireMock('sharp') as jest.Mock
    sharpMock.mockReturnValueOnce({
      webp: jest.fn().mockReturnThis(),
      toBuffer: jest.fn().mockRejectedValue(new Error('corrupt TIFF')),
    })
    ;(global.fetch as jest.Mock).mockResolvedValue(
      makeUpstreamResponse({ status: 200, body: 'bad-tiff', headers: {} }),
    )
    const allowed = encodeURIComponent(
      'https://examplestorage.blob.core.windows.net/content-assets/x.tif',
    )
    const res = await GET(makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`))
    expect(res.status).toBe(502)
  })
})

describe('GET /api/artifacts/image without AZURE_STORAGE_ACCOUNT', () => {
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
      'https://examplestorage.blob.core.windows.net/content-assets/x.jpg',
    )
    const res = await getUnconfigured(
      makeRequest(`http://localhost/api/artifacts/image?url=${allowed}`),
    )
    expect(res.status).toBe(503)
  })
})
