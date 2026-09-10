/**
 * @jest-environment node
 */
import {
  isEndMarker,
  getConnection,
  sendOnConnection,
  markIdle,
  closeConnection,
  isConnectionBusy,
} from '../ws-connection-manager'

// Mock logger
jest.mock('@/lib/logger', () => ({
  logger: {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  },
}))

// --- Mock WebSocket ---

interface MockWs {
  readyState: number
  OPEN: number
  CONNECTING: number
  CLOSED: number
  send: jest.Mock
  close: jest.Mock
  on: jest.Mock
  once: jest.Mock
  _onHandlers: Record<string, Function>
  _onceHandlers: Record<string, Function>
}

let lastCreatedWs: MockWs

function createMockWs(): MockWs {
  const onHandlers: Record<string, Function> = {}
  const onceHandlers: Record<string, Function> = {}

  const ws: MockWs = {
    readyState: 1,
    OPEN: 1,
    CONNECTING: 0,
    CLOSED: 3,
    send: jest.fn(),
    close: jest.fn(),
    on: jest.fn((event: string, handler: Function) => {
      onHandlers[event] = handler
    }),
    once: jest.fn((event: string, handler: Function) => {
      onceHandlers[event] = handler
    }),
    _onHandlers: onHandlers,
    _onceHandlers: onceHandlers,
  }

  lastCreatedWs = ws
  return ws
}

// Controls whether the mock auto-opens or auto-errors
let wsAutoOpen = true
let wsAutoError: Error | null = null

jest.mock('ws', () => ({
  __esModule: true,
  default: jest.fn(() => {
    const ws = createMockWs()
    // Schedule auto-open or auto-error on next tick (works with fake timers)
    setTimeout(() => {
      if (wsAutoError) {
        ws._onceHandlers['error']?.(wsAutoError)
      } else if (wsAutoOpen) {
        ws._onceHandlers['open']?.()
      }
    }, 0)
    return ws
  }),
}))

function makeHandlers() {
  return {
    onMessage: jest.fn(),
    onError: jest.fn(),
    onClose: jest.fn(),
  }
}

// Use fake timers for all tests in this file
beforeAll(() => {
  jest.useFakeTimers()
})

beforeEach(() => {
  jest.clearAllMocks()
  wsAutoOpen = true
  wsAutoError = null
  // Clean up global connection map between tests
  const g = globalThis as Record<string, unknown>
  if (g.__wsConnections) {
    const map = g.__wsConnections as Map<string, { idleTimer: ReturnType<typeof setTimeout> | null }>
    for (const conn of map.values()) {
      if (conn.idleTimer) clearTimeout(conn.idleTimer)
    }
    map.clear()
  }
})

afterEach(() => {
  // Clear any pending timers after each test
  jest.clearAllTimers()
})

afterAll(() => {
  jest.useRealTimers()
})

// ─── isEndMarker ─────────────────────────────────────────────────────

describe('isEndMarker', () => {
  test('returns true for plain [END]', () => {
    expect(isEndMarker('[END]')).toBe(true)
  })

  test('returns true for JSON-encoded "[END]"', () => {
    expect(isEndMarker('"[END]"')).toBe(true)
  })

  test('returns true for [END] with surrounding whitespace', () => {
    expect(isEndMarker('  [END]  ')).toBe(true)
  })

  test('returns false for regular messages', () => {
    expect(isEndMarker('hello world')).toBe(false)
    expect(isEndMarker('{"type":"delta","delta":"hi"}')).toBe(false)
  })

  test('returns false for partial matches', () => {
    expect(isEndMarker('[END] extra')).toBe(false)
    expect(isEndMarker('prefix [END]')).toBe(false)
  })

  test('returns false for empty strings', () => {
    expect(isEndMarker('')).toBe(false)
    expect(isEndMarker('   ')).toBe(false)
  })
})

const TEST_AUTH_TOKEN = 'test-token'

// Helper to get connection with fake timers
async function getConnectionWithTimers(sessionId: string, url: string) {
  const connPromise = getConnection(sessionId, url, TEST_AUTH_TOKEN)
  await jest.advanceTimersByTimeAsync(0) // Let mock WS "open"
  return connPromise
}

// ─── getConnection ───────────────────────────────────────────────────

describe('getConnection', () => {
  test('creates new connection and resolves when WS opens', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')

    expect(conn).toBeDefined()
    expect(conn.sessionId).toBe('session-1')
    expect(conn.ws).toBe(lastCreatedWs)
    expect(conn.busy).toBe(false)
  })

  test('reuses existing open connection (returns same object)', async () => {
    const conn1 = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    const firstWs = lastCreatedWs

    // Second call — should NOT create a new WS
    const conn2 = await getConnectionWithTimers('session-1', 'ws://localhost/ws')

    expect(conn2).toBe(conn1)
    expect(lastCreatedWs).toBe(firstWs)
  })

  test('replaces dead connection (readyState !== OPEN)', async () => {
    const conn1 = await getConnectionWithTimers('session-1', 'ws://localhost/ws')

    // Simulate connection death
    ;(conn1.ws as unknown as MockWs).readyState = 3 // CLOSED

    // New call should create a replacement
    const conn2 = await getConnectionWithTimers('session-1', 'ws://localhost/ws')

    expect(conn2).not.toBe(conn1)
    expect(conn2.sessionId).toBe('session-1')
  })

  test('passes Authorization: Bearer header on handshake (token not in URL)', async () => {
    await getConnectionWithTimers('session-auth', 'ws://localhost/ws')

    // Look up the mocked ws module's default export and inspect the call args
    const wsModule = jest.requireMock('ws') as { default: jest.Mock }
    const callArgs = wsModule.default.mock.calls.at(-1)
    expect(callArgs?.[0]).toBe('ws://localhost/ws')
    expect(callArgs?.[1]).toEqual({
      headers: { Authorization: `Bearer ${TEST_AUTH_TOKEN}` },
    })
  })

  test('late close event after replace does not delete the new connection', async () => {
    const conn1 = await getConnectionWithTimers('session-x', 'ws://localhost/ws')
    const firstWs = lastCreatedWs

    // Mark conn1's WS as dead so getConnection replaces it
    ;(conn1.ws as unknown as MockWs).readyState = 3 // CLOSED

    const conn2 = await getConnectionWithTimers('session-x', 'ws://localhost/ws')
    expect(conn2).not.toBe(conn1)

    // Now conn1's close fires late (after replacement) — must NOT evict conn2
    firstWs._onHandlers['close']?.(1006, Buffer.from('late'))

    const g = globalThis as Record<string, unknown>
    const map = g.__wsConnections as Map<string, unknown>
    expect(map.get('session-x')).toBe(conn2)
  })

  test('rejects when WS errors before opening', async () => {
    wsAutoOpen = false
    wsAutoError = new Error('Connection refused')

    const connPromise = getConnection('session-err', 'ws://localhost/ws', TEST_AUTH_TOKEN)

    // Set up the expectation before advancing timers
    const expectation = expect(connPromise).rejects.toThrow('Connection refused')

    await jest.advanceTimersByTimeAsync(0) // Let mock WS "error"

    await expectation
  })
})

// ─── sendOnConnection ────────────────────────────────────────────────

describe('sendOnConnection', () => {
  test('sends message on WS and attaches handlers', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    const handlers = makeHandlers()

    await sendOnConnection(conn, 'hello', handlers)

    expect(conn.ws.send).toHaveBeenCalledWith('hello')
    expect(conn.busy).toBe(true)
  })

  test('routes incoming messages to onMessage handler', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    const handlers = makeHandlers()
    await sendOnConnection(conn, 'hello', handlers)

    // Trigger message via persistent on('message') handler
    const msgData = '{"type":"delta","delta":"hi"}'
    lastCreatedWs._onHandlers['message'](msgData)

    expect(handlers.onMessage).toHaveBeenCalledWith(msgData)
  })

  test('returns detach function', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    const detach = await sendOnConnection(conn, 'hello', makeHandlers())

    expect(typeof detach).toBe('function')

    // Detach starts drain
    detach()
    expect(conn.draining).toBe(true)
  })

  test('awaits drain from previous abandoned response before sending', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    const detach = await sendOnConnection(conn, 'msg1', makeHandlers())

    // Abandon first response
    detach()
    expect(conn.draining).toBe(true)

    // Start second send — blocks on drain
    const handlers2 = makeHandlers()
    const sendPromise = sendOnConnection(conn, 'msg2', handlers2)

    // Simulate [END] arriving to complete the drain
    lastCreatedWs._onHandlers['message']('[END]')

    await sendPromise
    expect(conn.ws.send).toHaveBeenCalledWith('msg2')
    expect(conn.busy).toBe(true)
  })
})

// ─── markIdle ────────────────────────────────────────────────────────

describe('markIdle', () => {
  test('sets busy = false and clears handlers', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    conn.busy = true
    conn.handlers = makeHandlers()

    markIdle(conn)

    expect(conn.busy).toBe(false)
    expect(conn.handlers).toBeNull()
  })

  test('resets idle timer', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')

    markIdle(conn)

    expect(conn.idleTimer).not.toBeNull()
  })
})

// ─── closeConnection ─────────────────────────────────────────────────

describe('closeConnection', () => {
  test('closes WS and removes from map', async () => {
    await getConnectionWithTimers('session-1', 'ws://localhost/ws')

    closeConnection('session-1')

    expect(lastCreatedWs.close).toHaveBeenCalled()
    expect(isConnectionBusy('session-1')).toBe(false)
  })

  test('clears timers', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    expect(conn.idleTimer).not.toBeNull()

    closeConnection('session-1')

    expect(conn.idleTimer).toBeNull()
  })

  test('no-ops for unknown sessionId', () => {
    expect(() => closeConnection('nonexistent')).not.toThrow()
  })
})

// ─── isConnectionBusy ────────────────────────────────────────────────

describe('isConnectionBusy', () => {
  test('returns true when connection is busy', async () => {
    const conn = await getConnectionWithTimers('session-1', 'ws://localhost/ws')
    conn.busy = true

    expect(isConnectionBusy('session-1')).toBe(true)
  })

  test('returns false when idle', async () => {
    await getConnectionWithTimers('session-1', 'ws://localhost/ws')

    expect(isConnectionBusy('session-1')).toBe(false)
  })

  test('returns false for unknown session', () => {
    expect(isConnectionBusy('nonexistent')).toBe(false)
  })
})

// ─── Timeouts ────────────────────────────────────────────────────────

describe('timeouts', () => {
  test('idle timeout (5 min) auto-closes connection', async () => {
    // Don't auto-open — we'll trigger it manually
    wsAutoOpen = false
    const connPromise = getConnection('session-1', 'ws://localhost/ws', TEST_AUTH_TOKEN)
    await jest.advanceTimersByTimeAsync(0)
    lastCreatedWs._onceHandlers['open']()
    await connPromise

    // Not yet
    jest.advanceTimersByTime(5 * 60 * 1000 - 1)
    expect(lastCreatedWs.close).not.toHaveBeenCalled()

    // Now
    jest.advanceTimersByTime(1)
    expect(lastCreatedWs.close).toHaveBeenCalled()
  })

  test('drain timeout (30s) throws and closes connection', async () => {
    wsAutoOpen = false
    const connPromise = getConnection('session-1', 'ws://localhost/ws', TEST_AUTH_TOKEN)
    await jest.advanceTimersByTimeAsync(0)
    lastCreatedWs._onceHandlers['open']()
    const conn = await connPromise

    // First send + detach
    const detach = await sendOnConnection(conn, 'msg1', makeHandlers())
    detach()

    // Second send — awaits drain with 30s timeout
    const sendPromise = sendOnConnection(conn, 'msg2', makeHandlers())

    // Attach rejection handler BEFORE advancing timers to avoid unhandled rejection
    const rejectionExpectation = expect(sendPromise).rejects.toThrow(
      'WebSocket drain timeout'
    )

    // Advance past drain timeout
    await jest.advanceTimersByTimeAsync(30 * 1000)

    await rejectionExpectation
  })
})
