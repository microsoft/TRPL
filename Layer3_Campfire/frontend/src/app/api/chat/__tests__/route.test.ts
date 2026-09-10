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

jest.mock('@/lib/ws-connection-manager', () => ({
  getConnection: jest.fn(),
  sendOnConnection: jest.fn(),
  markIdle: jest.fn(),
  isConnectionBusy: jest.fn(),
  isEndMarker: (data: string) => data.trim() === '[END]',
}))

import {
  getConnection,
  sendOnConnection,
  markIdle,
  isConnectionBusy,
} from '@/lib/ws-connection-manager'
import { POST } from '../route'

type MockHandlers = {
  onMessage: (data: string) => void
  onError: (error: Error) => void
  onClose: (code: number, reason: string) => void
}

const mockGetConnection = getConnection as jest.MockedFunction<typeof getConnection>
const mockSendOnConnection = sendOnConnection as jest.MockedFunction<typeof sendOnConnection>
const mockMarkIdle = markIdle as jest.MockedFunction<typeof markIdle>
const mockIsConnectionBusy = isConnectionBusy as jest.MockedFunction<typeof isConnectionBusy>

function signedCookie(userId: string): string {
  const sig = createHmac('sha256', 'test-key').update(userId).digest('base64url')
  return `${userId}.${sig}`
}

function makeRequest(body: unknown, cookieUserId?: string): NextRequest {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (cookieUserId) headers.cookie = `rr_uid=${signedCookie(cookieUserId)}`
  return new NextRequest('http://localhost/api/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
}

async function readSSEEvents(response: Response): Promise<string[]> {
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
  }
  return buffer
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('data: '))
    .map((line) => line.slice(6))
}

beforeEach(() => {
  jest.useRealTimers()
  jest.clearAllMocks()
  mockIsConnectionBusy.mockReturnValue(false)
  mockGetConnection.mockResolvedValue({
    ws: {} as never,
    sessionId: 'session-1',
    wsUrl: 'ws://backend.test/ws/chat',
    busy: false,
    draining: false,
    drainPromise: null,
    idleTimer: null,
    handlers: null,
  } as never)
  mockSendOnConnection.mockImplementation(async (_conn, _payload, _handlers) => {
    return () => {}
  })
  delete process.env.COOKIE_SECURE
})

describe('POST /api/chat', () => {
  test('returns 400 when request body fails schema validation', async () => {
    const res = await POST(makeRequest({ message: '' }))
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toBe('Invalid request')
  })

  test('returns 400 when sessionId is missing', async () => {
    const res = await POST(makeRequest({ message: 'hello' }))
    expect(res.status).toBe(400)
  })

  test('returns 409 when connection is already busy', async () => {
    mockIsConnectionBusy.mockReturnValue(true)
    const res = await POST(makeRequest({ message: 'hello', sessionId: 'sess-1' }))
    expect(res.status).toBe(409)
    const body = await res.json()
    expect(body.error).toMatch(/already in progress/i)
  })

  test('streams delta + final + [DONE] for happy path', async () => {
    let capturedHandlers: MockHandlers | undefined
    mockSendOnConnection.mockImplementation(async (_conn, _payload, handlers) => {
      capturedHandlers = handlers as MockHandlers
      return () => {}
    })

    const responsePromise = POST(makeRequest({ message: 'hi TR', sessionId: 'sess-1' }))
    const response = await responsePromise
    expect(response.status).toBe(200)
    expect(response.headers.get('Content-Type')).toBe('text/event-stream')

    // Simulate backend messages
    setTimeout(() => {
      capturedHandlers!.onMessage(JSON.stringify({ type: 'delta', delta: 'Hello ' }))
      capturedHandlers!.onMessage(JSON.stringify({ type: 'delta', delta: 'world' }))
      capturedHandlers!.onMessage(
        JSON.stringify({ type: 'final', text: 'Hello world', citations: [] })
      )
      capturedHandlers!.onMessage('[END]')
    }, 0)

    const events = await readSSEEvents(response)
    const parsed = events.map((e) => (e === '"[DONE]"' ? '[DONE]' : JSON.parse(e)))

    expect(parsed[0]).toEqual({ type: 'delta', content: 'Hello ' })
    expect(parsed[1]).toEqual({ type: 'delta', content: 'world' })
    expect(parsed[2]).toMatchObject({ type: 'final', content: 'Hello world' })
    expect(parsed[3]).toBe('[DONE]')
    expect(mockMarkIdle).toHaveBeenCalled()
  })

  test('emits agent_error when backend yields error type', async () => {
    let capturedHandlers: MockHandlers | undefined
    mockSendOnConnection.mockImplementation(async (_conn, _payload, handlers) => {
      capturedHandlers = handlers as MockHandlers
      return () => {}
    })

    const response = await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))
    setTimeout(() => {
      capturedHandlers!.onMessage(JSON.stringify({ type: 'error', error: 'agent failed' }))
    }, 0)

    const events = await readSSEEvents(response)
    const first = JSON.parse(events[0])
    expect(first).toEqual({ type: 'agent_error', error: 'agent failed' })
    expect(mockMarkIdle).toHaveBeenCalled()
  })

  test('emits error and [DONE] when WebSocket fails to open', async () => {
    mockGetConnection.mockRejectedValue(new Error('ECONNREFUSED'))

    const response = await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))
    const events = await readSSEEvents(response)
    const first = JSON.parse(events[0])
    expect(first.type).toBe('error')
    expect(first.error).toMatch(/Failed to connect/i)
  })

  test('emits error event when WS closes mid-stream', async () => {
    let capturedHandlers: MockHandlers | undefined
    mockSendOnConnection.mockImplementation(async (_conn, _payload, handlers) => {
      capturedHandlers = handlers as MockHandlers
      return () => {}
    })

    const response = await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))
    setTimeout(() => {
      capturedHandlers!.onClose(1006, 'abnormal')
    }, 0)

    const events = await readSSEEvents(response)
    const errorEvent = events.map((e) => JSON.parse(e)).find((e) => e?.type === 'error')
    expect(errorEvent.error).toMatch(/Connection to chat service was lost/i)
  })

  test('deduplicates citations and transforms to attachments', async () => {
    let capturedHandlers: MockHandlers | undefined
    mockSendOnConnection.mockImplementation(async (_conn, _payload, handlers) => {
      capturedHandlers = handlers as MockHandlers
      return () => {}
    })

    const response = await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))

    setTimeout(() => {
      capturedHandlers!.onMessage(
        JSON.stringify({
          type: 'final',
          text: 'done',
          citations: [
            {
              id: 'l1',
              source: 'letter',
              title: 'Letter A',
              record_id: 'rec-1',
              trpl_file_url: ['https://img.test/1.jpg'],
            },
            // Duplicate record_id — should be removed
            {
              id: 'l2',
              source: 'letter',
              title: 'Letter A (dup)',
              record_id: 'rec-1',
              trpl_file_url: ['https://img.test/1.jpg'],
            },
            // Book with no URL — kept (books allowed)
            {
              id: 'b1',
              source: 'book',
              book_title: 'Rough Riders',
              chapter_id: 'ch-1',
            },
            // Letter with no URL — filtered out
            {
              id: 'l3',
              source: 'letter',
              record_id: 'rec-2',
              title: 'No URL letter',
            },
          ],
        })
      )
      capturedHandlers!.onMessage('[END]')
    }, 0)

    const events = await readSSEEvents(response)
    const finalEvent = events.map((e) => (e === '"[DONE]"' ? null : JSON.parse(e))).find(
      (e) => e && e.type === 'final'
    )
    expect(finalEvent.attachments).toHaveLength(2)
    expect(finalEvent.attachments[0].id).toBe('l1')
    expect(finalEvent.attachments[1].id).toBe('b1')
  })

  test('passes progress events through to client', async () => {
    let capturedHandlers: MockHandlers | undefined
    mockSendOnConnection.mockImplementation(async (_conn, _payload, handlers) => {
      capturedHandlers = handlers as MockHandlers
      return () => {}
    })

    const response = await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))
    setTimeout(() => {
      capturedHandlers!.onMessage(JSON.stringify({ type: 'progress', progress: 'Searching...' }))
      capturedHandlers!.onMessage('[END]')
    }, 0)

    const events = await readSSEEvents(response)
    const progressEvent = events
      .map((e) => (e === '"[DONE]"' ? null : JSON.parse(e)))
      .find((e) => e && e.type === 'progress')
    expect(progressEvent.progress).toBe('Searching...')
  })

  test('uses cookie user_id (not request body) in payload sent to backend', async () => {
    await POST(
      makeRequest(
        {
          message: 'hi',
          sessionId: 'sess-1',
          agent: 'default',
          userId: 'attacker-spoofed',
        },
        'real-owner'
      )
    )

    expect(mockSendOnConnection).toHaveBeenCalled()
    const payload = JSON.parse(mockSendOnConnection.mock.calls[0][1] as string)
    expect(payload).toEqual({
      message: 'hi',
      chat_id: 'sess-1',
      agent: 'default',
      user_id: 'real-owner',
    })
  })

  test('mints and sets a signed rr_uid cookie when none is present', async () => {
    const res = await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))
    expect(res.headers.get('set-cookie')).toMatch(/^rr_uid=/)
    expect(res.headers.get('set-cookie')).toMatch(/HttpOnly/i)

    // The minted user_id is what's sent to the backend, not the (missing)
    // body field.
    const payload = JSON.parse(mockSendOnConnection.mock.calls[0][1] as string)
    expect(typeof payload.user_id).toBe('string')
    expect(payload.user_id.length).toBeGreaterThan(0)
  })

  test('rejects an invalid COOKIE_SECURE setting', async () => {
    process.env.COOKIE_SECURE = 'tru'

    const response = await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))

    expect(response.status).toBe(500)
    expect(response.headers.get('set-cookie')).toBeNull()
  })

  test('builds wsUrl with chat_id query param and passes token separately', async () => {
    await POST(makeRequest({ message: 'hi', sessionId: 'sess-1' }))
    expect(mockGetConnection).toHaveBeenCalled()
    const [, url, authToken] = mockGetConnection.mock.calls[0]
    expect(url).toContain('ws://backend.test/ws/chat')
    expect(url).toContain('chat_id=sess-1')
    // Token must NOT appear in the URL — it travels in the Authorization
    // header attached by the connection manager.
    expect(url).not.toMatch(/token=/)
    expect(authToken).toBe('test-key')
  })

  test('returns 500 when request.json() throws', async () => {
    const req = new NextRequest('http://localhost/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: 'not-json{',
    })
    const res = await POST(req)
    expect(res.status).toBe(500)
  })
})
