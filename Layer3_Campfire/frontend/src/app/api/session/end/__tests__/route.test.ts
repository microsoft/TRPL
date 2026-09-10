/**
 * @jest-environment node
 */
import { POST } from '../route'
import { NextRequest } from 'next/server'
import { closeConnection } from '@/lib/ws-connection-manager'

jest.mock('@/lib/ws-connection-manager', () => ({
  closeConnection: jest.fn(),
}))

jest.mock('@/lib/logger', () => ({
  logger: {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  },
}))

function makeRequest(body: unknown) {
  return new NextRequest('http://localhost/api/session/end', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe('POST /api/session/end', () => {
  test('returns 200 with { success: true } for valid sessionId', async () => {
    const res = await POST(makeRequest({ sessionId: 'abc-123' }))
    const json = await res.json()

    expect(res.status).toBe(200)
    expect(json).toEqual({ success: true })
  })

  test('calls closeConnection with the sessionId', async () => {
    await POST(makeRequest({ sessionId: 'abc-123' }))

    expect(closeConnection).toHaveBeenCalledWith('abc-123')
  })

  test('returns 400 when sessionId is missing', async () => {
    const res = await POST(makeRequest({}))
    const json = await res.json()

    expect(res.status).toBe(400)
    expect(json).toEqual({ error: 'sessionId is required' })
  })

  test('returns 400 when sessionId is not a string', async () => {
    const res = await POST(makeRequest({ sessionId: 12345 }))
    const json = await res.json()

    expect(res.status).toBe(400)
    expect(json).toEqual({ error: 'sessionId is required' })
  })

  test('returns 500 on unexpected error', async () => {
    ;(closeConnection as jest.Mock).mockImplementation(() => {
      throw new Error('unexpected failure')
    })

    const res = await POST(makeRequest({ sessionId: 'abc-123' }))
    const json = await res.json()

    expect(res.status).toBe(500)
    expect(json).toEqual({ error: 'Internal server error' })
  })
})
