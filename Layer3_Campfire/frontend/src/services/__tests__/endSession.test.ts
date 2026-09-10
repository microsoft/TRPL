/**
 * @jest-environment jsdom
 */
import { endSession } from '../chat'
import { API_ROUTES } from '@/lib/constants'

describe('endSession', () => {
  const originalSendBeacon = navigator.sendBeacon
  const originalFetch = global.fetch

  beforeEach(() => {
    jest.restoreAllMocks()
    // Reset sendBeacon to a fresh mock
    Object.defineProperty(navigator, 'sendBeacon', {
      value: jest.fn(() => true),
      writable: true,
      configurable: true,
    })
    // Reset fetch
    global.fetch = jest.fn(() => Promise.resolve({} as Response))
  })

  afterAll(() => {
    // Restore originals
    Object.defineProperty(navigator, 'sendBeacon', {
      value: originalSendBeacon,
      writable: true,
      configurable: true,
    })
    global.fetch = originalFetch
  })

  test('uses sendBeacon when available and returns', () => {
    ;(navigator.sendBeacon as jest.Mock).mockReturnValue(true)

    endSession('test-session')

    expect(navigator.sendBeacon).toHaveBeenCalledWith(
      API_ROUTES.SESSION_END,
      expect.any(Blob)
    )
    // Should NOT fall through to fetch
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('passes correct URL and JSON body to sendBeacon', () => {
    ;(navigator.sendBeacon as jest.Mock).mockReturnValue(true)

    endSession('my-session-id')

    const blob = (navigator.sendBeacon as jest.Mock).mock.calls[0][1] as Blob
    expect(blob.type).toBe('application/json')
    expect(navigator.sendBeacon).toHaveBeenCalledWith(
      '/api/session/end',
      expect.any(Blob)
    )
  })

  test('falls back to fetch when sendBeacon is unavailable', () => {
    Object.defineProperty(navigator, 'sendBeacon', {
      value: undefined,
      writable: true,
      configurable: true,
    })

    endSession('test-session')

    expect(global.fetch).toHaveBeenCalledWith(
      API_ROUTES.SESSION_END,
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId: 'test-session' }),
        keepalive: true,
      })
    )
  })

  test('falls back to fetch when sendBeacon returns false', () => {
    ;(navigator.sendBeacon as jest.Mock).mockReturnValue(false)

    endSession('test-session')

    expect(navigator.sendBeacon).toHaveBeenCalled()
    expect(global.fetch).toHaveBeenCalledWith(
      API_ROUTES.SESSION_END,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ sessionId: 'test-session' }),
      })
    )
  })

  test('silently catches fetch errors', () => {
    Object.defineProperty(navigator, 'sendBeacon', {
      value: undefined,
      writable: true,
      configurable: true,
    })
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('Network error'))

    // Should not throw
    expect(() => endSession('test-session')).not.toThrow()
  })
})
