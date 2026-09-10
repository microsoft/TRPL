import { NextRequest, NextResponse } from 'next/server'
import { closeConnection } from '@/lib/ws-connection-manager'
import { logger } from '@/lib/logger'

/**
 * POST /api/session/end
 *
 * Closes the persistent WebSocket connection for a session.
 * Called when the user navigates away from chat or the page unloads.
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const sessionId = body?.sessionId

    if (!sessionId || typeof sessionId !== 'string') {
      return NextResponse.json(
        { error: 'sessionId is required' },
        { status: 400 }
      )
    }

    logger.info('Session end requested', { sessionId })
    closeConnection(sessionId)

    return NextResponse.json({ success: true })
  } catch (error) {
    logger.error('Session end error', {
      error: error instanceof Error ? error.message : 'Unknown',
    })
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
