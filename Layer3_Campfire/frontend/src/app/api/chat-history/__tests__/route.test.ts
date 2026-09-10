/**
 * @jest-environment node
 */
import { NextRequest } from 'next/server'
import { createHmac } from 'node:crypto'

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

import { GET, DELETE } from '../[...path]/route'

function signedCookie(userId: string): string {
  const sig = createHmac('sha256', 'test-key').update(userId).digest('base64url')
  return `${userId}.${sig}`
}

function makeRequest(method: 'GET' | 'DELETE', cookieUserId?: string): NextRequest {
  const headers: Record<string, string> = {}
  if (cookieUserId) headers.cookie = `rr_uid=${signedCookie(cookieUserId)}`
  return new NextRequest('http://localhost/api/chat-history/foo', { method, headers })
}

function context(segments?: string[]) {
  return { params: Promise.resolve({ path: segments }) }
}

beforeEach(() => {
  jest.clearAllMocks()
  global.fetch = jest.fn() as unknown as typeof fetch
})

describe('GET /api/chat-history/[...path]', () => {
  test('proxies single-segment path scoped to the cookie user_id', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 200,
      text: async () => '["chat-a","chat-b"]',
      headers: new Map([['content-type', 'application/json']]),
    })

    const res = await GET(makeRequest('GET', 'user-1'), context(['user-1']))
    expect(res.status).toBe(200)
    expect(await res.text()).toBe('["chat-a","chat-b"]')

    expect(global.fetch).toHaveBeenCalledWith(
      'http://backend.test/api/chat-history/user-1',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ Authorization: 'Bearer test-key' }),
      })
    )
  })

  test('overrides URL user_id with the cookie user_id (defeats IDOR)', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 200,
      text: async () => '[]',
      headers: new Map([['content-type', 'application/json']]),
    })

    // Client tries to read victim's chats by putting victim's id in the URL.
    // The proxy must replace it with the cookie owner's id.
    await GET(makeRequest('GET', 'attacker'), context(['victim']))
    const fetchUrl = (global.fetch as jest.Mock).mock.calls[0][0]
    expect(fetchUrl).toBe('http://backend.test/api/chat-history/attacker')
  })

  test('proxies three-segment messages path scoped to cookie user_id', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 200,
      text: async () => '[]',
      headers: new Map([['content-type', 'application/json']]),
    })

    const res = await GET(makeRequest('GET', 'user-1'), context(['victim', 'chat-a', 'messages']))
    expect(res.status).toBe(200)
    const fetchUrl = (global.fetch as jest.Mock).mock.calls[0][0]
    expect(fetchUrl).toBe('http://backend.test/api/chat-history/user-1/chat-a/messages')
  })

  test('returns 400 for invalid segment combinations', async () => {
    const res = await GET(makeRequest('GET', 'user-1'), context(['user-1', 'chat-a']))
    expect(res.status).toBe(400)
    expect(await res.json()).toEqual({ error: 'Invalid chat history route' })
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('returns 400 for empty path', async () => {
    const res = await GET(makeRequest('GET', 'user-1'), context([]))
    expect(res.status).toBe(400)
  })

  test('returns 500 when fetch throws', async () => {
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network down'))

    const res = await GET(makeRequest('GET', 'user-1'), context(['user-1']))
    expect(res.status).toBe(500)
    expect(await res.json()).toEqual({ error: 'Failed to fetch chat history' })
  })

  test('forwards backend status code', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 500,
      text: async () => 'Backend error',
      headers: new Map([['content-type', 'application/json']]),
    })

    const res = await GET(makeRequest('GET', 'user-1'), context(['user-1']))
    expect(res.status).toBe(500)
  })

  test('mints and sets a signed cookie when no cookie is present', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 200,
      text: async () => '[]',
      headers: new Map([['content-type', 'application/json']]),
    })

    const res = await GET(makeRequest('GET'), context(['victim']))
    expect(res.status).toBe(200)
    const setCookie = res.headers.get('set-cookie') ?? ''
    expect(setCookie).toMatch(/^rr_uid=/)
    expect(setCookie).toMatch(/HttpOnly/i)
    expect(setCookie).toMatch(/SameSite=lax/i)

    // The fresh cookie's user_id (not 'victim') is what we scope on
    const fetchUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string
    expect(fetchUrl).not.toContain('victim')
  })

  test('rejects a tampered cookie and mints a new one', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 200,
      text: async () => '[]',
      headers: new Map([['content-type', 'application/json']]),
    })

    // Cookie value without a signature dot — clearly forged
    const req = new NextRequest('http://localhost/api/chat-history/foo', {
      method: 'GET',
      headers: { cookie: 'rr_uid=victim-id-no-sig' },
    })
    const res = await GET(req, context(['victim-id-no-sig']))
    expect(res.headers.get('set-cookie')).toMatch(/^rr_uid=/)
    const fetchUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string
    expect(fetchUrl).not.toContain('victim-id-no-sig')
  })
})

describe('DELETE /api/chat-history/[...path]', () => {
  test('proxies two-segment delete scoped to cookie user_id', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 200,
      text: async () => '{"status":"success"}',
      headers: new Map([['content-type', 'application/json']]),
    })

    const res = await DELETE(makeRequest('DELETE', 'user-1'), context(['user-1', 'chat-a']))
    expect(res.status).toBe(200)
    expect(global.fetch).toHaveBeenCalledWith(
      'http://backend.test/api/chat-history/user-1/chat-a',
      expect.objectContaining({ method: 'DELETE' })
    )
  })

  test('overrides URL user_id with cookie user_id on DELETE', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      status: 200,
      text: async () => '{"status":"success"}',
      headers: new Map([['content-type', 'application/json']]),
    })

    await DELETE(makeRequest('DELETE', 'attacker'), context(['victim', 'chat-a']))
    const fetchUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string
    expect(fetchUrl).toBe('http://backend.test/api/chat-history/attacker/chat-a')
  })

  test('returns 400 for single-segment DELETE', async () => {
    const res = await DELETE(makeRequest('DELETE', 'user-1'), context(['user-1']))
    expect(res.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('returns 400 for three-segment DELETE', async () => {
    const res = await DELETE(makeRequest('DELETE', 'user-1'), context(['user-1', 'chat-a', 'extra']))
    expect(res.status).toBe(400)
  })

  test('returns 500 when fetch throws', async () => {
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network down'))

    const res = await DELETE(makeRequest('DELETE', 'user-1'), context(['user-1', 'chat-a']))
    expect(res.status).toBe(500)
  })
})
