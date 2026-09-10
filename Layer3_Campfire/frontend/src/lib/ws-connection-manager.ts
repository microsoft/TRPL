/**
 * WebSocket Connection Manager
 *
 * Maintains persistent WebSocket connections to the Python RAG backend,
 * keyed by sessionId. Connections survive across multiple chat messages
 * so the backend can maintain conversation context in-memory.
 *
 * Uses globalThis to survive HMR in development.
 */

import { logger } from '@/lib/logger'

const IDLE_TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes
const DRAIN_TIMEOUT_MS = 30 * 1000 // 30 seconds

/**
 * Check if a WebSocket message is the [END] marker.
 * Handles both plain-text "[END]" and JSON-encoded '"[END]"' (older backend).
 */
export function isEndMarker(data: string): boolean {
  const trimmed = data.trim()
  if (trimmed === '[END]') return true
  // Handle JSON-encoded [END] from backend versions before fix 0384613
  try {
    return JSON.parse(trimmed) === '[END]'
  } catch {
    return false
  }
}

/** Per-request message/error/close handlers swapped on each send */
export interface RequestHandlers {
  onMessage: (data: string) => void
  onError: (error: Error) => void
  onClose: (code: number, reason: string) => void
}

/** Internal connection state */
interface ManagedConnection {
  ws: InstanceType<typeof import('ws').default>
  sessionId: string
  wsUrl: string
  authToken: string
  busy: boolean
  draining: boolean
  drainPromise: Promise<void> | null
  idleTimer: ReturnType<typeof setTimeout> | null
  handlers: RequestHandlers | null
}

/** Map of sessionId -> ManagedConnection, stored on globalThis for HMR survival */
const GLOBAL_KEY = '__wsConnections' as const

function getConnectionMap(): Map<string, ManagedConnection> {
  const g = globalThis as Record<string, unknown>
  if (!g[GLOBAL_KEY]) {
    g[GLOBAL_KEY] = new Map<string, ManagedConnection>()
  }
  return g[GLOBAL_KEY] as Map<string, ManagedConnection>
}

/**
 * Remove an entry from the connection map only if it still points at `conn`.
 *
 * A close event can fire after an error already replaced the entry; without
 * this guard, the late close would delete the new (live) connection.
 */
function deleteFromMapIfCurrent(
  sessionId: string,
  conn: ManagedConnection
): void {
  const map = getConnectionMap()
  if (map.get(sessionId) === conn) {
    map.delete(sessionId)
  }
}

/** Reset the idle timer — called after each [END] */
function resetIdleTimer(conn: ManagedConnection) {
  if (conn.idleTimer) {
    clearTimeout(conn.idleTimer)
  }
  conn.idleTimer = setTimeout(() => {
    logger.info('Idle timeout reached, closing WebSocket', {
      sessionId: conn.sessionId,
    })
    closeConnection(conn.sessionId)
  }, IDLE_TIMEOUT_MS)
  // Don't keep the process alive just for idle cleanup
  conn.idleTimer.unref()
}

/**
 * Get an existing open connection or create a new one.
 * If the existing connection is dead (readyState !== OPEN), it is replaced.
 *
 * `authToken` is sent as the `Authorization: Bearer ...` header on the
 * handshake — never put it in the URL, since query strings end up in proxy
 * access logs.
 */
export async function getConnection(
  sessionId: string,
  wsUrl: string,
  authToken: string
): Promise<ManagedConnection> {
  const map = getConnectionMap()
  const existing = map.get(sessionId)

  // Reuse if open
  if (existing && existing.ws.readyState === existing.ws.OPEN) {
    logger.info('Reusing existing WebSocket connection', { sessionId })
    return existing
  }

  // Clean up stale entry
  if (existing) {
    logger.info('Replacing dead WebSocket connection', {
      sessionId,
      readyState: existing.ws.readyState,
    })
    cleanupConnection(existing)
    deleteFromMapIfCurrent(sessionId, existing)
  }

  // Create new connection
  const WebSocket = (await import('ws')).default
  const ws = new WebSocket(wsUrl, {
    headers: { Authorization: `Bearer ${authToken}` },
  })

  const conn: ManagedConnection = {
    ws,
    sessionId,
    wsUrl,
    authToken,
    busy: false,
    draining: false,
    drainPromise: null,
    idleTimer: null,
    handlers: null,
  }

  // Wire up persistent event handlers
  ws.on('message', (data: Buffer | string) => {
    const messageStr = data.toString()
    if (conn.handlers) {
      conn.handlers.onMessage(messageStr)
    }
  })

  ws.on('error', (error: Error) => {
    logger.error('WebSocket error, removing connection', {
      sessionId,
      error: error.message,
    })
    if (conn.handlers) {
      conn.handlers.onError(error)
    }
    // Backend may be in a bad state — remove the connection entirely
    cleanupConnection(conn)
    deleteFromMapIfCurrent(sessionId, conn)
  })

  ws.on('close', (code: number, reason: Buffer) => {
    const reasonStr = reason?.toString() || ''
    logger.info('WebSocket closed by remote', {
      sessionId,
      code,
      reason: reasonStr,
    })
    if (conn.handlers) {
      conn.handlers.onClose(code, reasonStr)
    }
    cleanupConnection(conn)
    deleteFromMapIfCurrent(sessionId, conn)
  })

  // Wait for connection to open
  await new Promise<void>((resolve, reject) => {
    ws.once('open', () => {
      logger.info('WebSocket connected', { sessionId, url: wsUrl })
      resolve()
    })
    ws.once('error', (err: Error) => {
      reject(err)
    })
  })

  map.set(sessionId, conn)
  resetIdleTimer(conn)

  return conn
}

/**
 * Send a message on an existing connection, attaching per-request handlers.
 *
 * If the connection is draining (previous response was abandoned), waits for
 * the drain to complete before sending.
 *
 * Returns a detach function that the caller should invoke on abort
 * (installs a drain handler to silently consume remaining messages).
 */
export async function sendOnConnection(
  conn: ManagedConnection,
  message: string,
  handlers: RequestHandlers
): Promise<() => void> {
  // Wait for drain from a previous abandoned response
  if (conn.draining && conn.drainPromise) {
    logger.info('Waiting for drain to complete before sending', {
      sessionId: conn.sessionId,
    })
    await Promise.race([
      conn.drainPromise,
      new Promise<void>((resolve) => setTimeout(resolve, DRAIN_TIMEOUT_MS)),
    ])
    // If still draining after timeout, the connection is likely stuck
    if (conn.draining) {
      logger.warn('Drain timeout, closing and replacing connection', {
        sessionId: conn.sessionId,
      })
      cleanupConnection(conn)
      deleteFromMapIfCurrent(conn.sessionId, conn)
      throw new Error('WebSocket drain timeout')
    }
  }

  // Clear idle timer while we're active
  if (conn.idleTimer) {
    clearTimeout(conn.idleTimer)
    conn.idleTimer = null
  }

  conn.busy = true
  conn.handlers = handlers
  conn.ws.send(message)

  // Return detach function (called on SSE abort)
  return () => {
    if (!conn.busy) return // Already finished cleanly

    logger.info('Detaching handlers, starting drain', {
      sessionId: conn.sessionId,
    })

    conn.draining = true
    conn.drainPromise = new Promise<void>((resolve) => {
      // Install silent drain handler that discards messages until [END]
      conn.handlers = {
        onMessage: (data: string) => {
          if (isEndMarker(data)) {
            conn.busy = false
            conn.draining = false
            conn.drainPromise = null
            conn.handlers = null
            resetIdleTimer(conn)
            resolve()
          }
          // Otherwise silently discard
        },
        onError: () => {
          conn.busy = false
          conn.draining = false
          conn.drainPromise = null
          conn.handlers = null
          resolve()
        },
        onClose: () => {
          conn.busy = false
          conn.draining = false
          conn.drainPromise = null
          conn.handlers = null
          resolve()
        },
      }
    })
  }
}

/**
 * Mark the connection as no longer busy and reset the idle timer.
 * Called by the chat route handler when [END] is received.
 */
export function markIdle(conn: ManagedConnection) {
  conn.busy = false
  conn.handlers = null
  resetIdleTimer(conn)
}

/**
 * Explicitly close and remove a connection (session end / cleanup).
 */
export function closeConnection(sessionId: string) {
  const map = getConnectionMap()
  const conn = map.get(sessionId)
  if (!conn) return

  logger.info('Closing WebSocket connection', { sessionId })
  cleanupConnection(conn)
  deleteFromMapIfCurrent(sessionId, conn)
}

/**
 * Check if a connection exists and is busy.
 */
export function isConnectionBusy(sessionId: string): boolean {
  const map = getConnectionMap()
  const conn = map.get(sessionId)
  return conn?.busy ?? false
}

/** Internal cleanup — close WS and clear timers */
function cleanupConnection(conn: ManagedConnection) {
  if (conn.idleTimer) {
    clearTimeout(conn.idleTimer)
    conn.idleTimer = null
  }
  conn.handlers = null
  conn.busy = false
  conn.draining = false
  conn.drainPromise = null

  try {
    if (
      conn.ws.readyState === conn.ws.OPEN ||
      conn.ws.readyState === conn.ws.CONNECTING
    ) {
      conn.ws.close()
    }
  } catch {
    // Already closed
  }
}
