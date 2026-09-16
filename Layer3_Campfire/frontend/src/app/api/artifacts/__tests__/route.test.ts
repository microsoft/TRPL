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
  },
}))

jest.mock('@/lib/logger', () => ({
  logger: {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  },
}))

import { POST } from '../route'

function makeRequest(body: unknown): NextRequest {
  return new NextRequest('http://localhost/api/artifacts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

beforeEach(() => {
  jest.clearAllMocks()
  global.fetch = jest.fn() as unknown as typeof fetch
})

describe('POST /api/artifacts', () => {
  test('returns 400 when index is missing', async () => {
    const res = await POST(makeRequest({ id: 'x' }))
    expect(res.status).toBe(400)
    expect((await res.json()).error).toMatch(/index and id/i)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('returns 400 when id is missing', async () => {
    const res = await POST(makeRequest({ index: 'letter' }))
    expect(res.status).toBe(400)
  })

  test('proxies to backend and returns data on success', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ id: 'letter-1', title: 'To Cabot Lodge' }),
    })

    const res = await POST(makeRequest({ index: 'letter', id: 'letter-1' }))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ id: 'letter-1', title: 'To Cabot Lodge' })

    expect(global.fetch).toHaveBeenCalledWith(
      'http://backend.test/api/artifacts',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-key',
        }),
        body: JSON.stringify({ index: 'letter', id: 'letter-1' }),
      })
    )
  })

  test('forwards backend non-OK status with error text', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 404,
      text: async () => 'Artifact not found',
    })

    const res = await POST(makeRequest({ index: 'letter', id: 'missing' }))
    expect(res.status).toBe(404)
    expect((await res.json()).error).toBe('Artifact not found')
  })

  test('returns 500 when fetch rejects', async () => {
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network down'))

    const res = await POST(makeRequest({ index: 'book', id: 'book-1' }))
    expect(res.status).toBe(500)
    expect((await res.json()).error).toBe('Internal server error')
  })

  test('returns 500 when body is not valid JSON', async () => {
    const req = new NextRequest('http://localhost/api/artifacts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: 'not-json{',
    })
    const res = await POST(req)
    expect(res.status).toBe(500)
  })
})
