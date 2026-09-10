import { NextRequest, NextResponse } from 'next/server'
import { env } from '@/lib/env'
import { ChatRequestSchema } from '@/schemas/chat'
import { logger } from '@/lib/logger'
import { resolveUserId, setUserIdCookie } from '@/lib/userIdCookie'
import {
  getConnection,
  sendOnConnection,
  markIdle,
  isConnectionBusy,
  isEndMarker,
} from '@/lib/ws-connection-manager'
/**
 * Transform backend citations to frontend attachments format
 */
function deduplicateCitations(citations: BackendCitation[]): BackendCitation[] {
  const seen = new Set<string>()
  return citations.filter((c) => {
    const key = c.record_id || c.chapter_id
    if (!key) return true
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function transformCitations(citations: BackendCitation[]): Attachment[] {
  return deduplicateCitations(citations)
    // Allow letters with URLs, and all book citations regardless of URLs
    .filter((c) => c.source === 'book' || c.trpl_file_url?.length || c.trc_url)
    .map((citation, index) => {
      const isLetter = citation.source === 'letter'
      const imageUrl = citation.trpl_file_url?.[0]

      return {
        id: citation.id || `citation-${index}`,
        type: isLetter ? 'letter' : 'document',
        title: citation.title || citation.book_title || 'Untitled',
        url: imageUrl || citation.trc_url || '',
        // Pass through backend fields as-is
        source: citation.source,
        ...(citation.trc_url && { trc_url: citation.trc_url }),
        ...(citation.record_id && { record_id: citation.record_id }),
        ...(citation.repository && { repository: citation.repository }),
        ...(citation.collection && { collection: citation.collection }),
        ...(citation.creation_date && { creation_date: citation.creation_date }),
        ...(citation.book_title && { book_title: citation.book_title }),
        ...(citation.book_authors && { book_authors: citation.book_authors }),
        ...(citation.book_publisher && { book_publisher: citation.book_publisher }),
        ...(citation.book_published_date && { book_published_date: citation.book_published_date }),
        ...(citation.book_isbn && { book_isbn: citation.book_isbn }),
        ...(citation.chapter_title && { chapter_title: citation.chapter_title }),
        ...(citation.text && { text: citation.text }),
        ...(citation.description && { description: citation.description }),
        ...(citation.book_subjects && { book_subjects: citation.book_subjects }),
        ...(citation.book_description && { book_description: citation.book_description }),
        ...(citation.book_language && { book_language: citation.book_language }),
        ...(citation.chapter_id && { chapter_id: citation.chapter_id }),
        ...(citation.paragraph_ids && { paragraph_ids: citation.paragraph_ids }),
      }
    })
    .filter((a) => a.url || a.source === 'book') as Attachment[]
}

/**
 * Backend citation structure (from Azure AI Search results).
 * Letters and books share some fields; type-specific fields are optional.
 */
interface BackendCitation {
  id?: string
  source?: 'letter' | 'book'
  title?: string
  trc_url?: string
  trpl_file_url?: string[]
  // Letter-specific
  record_id?: string
  repository?: string
  collection?: string
  creation_date?: string
  // Book-specific
  book_title?: string
  book_authors?: string
  book_publisher?: string
  book_published_date?: string
  book_isbn?: string
  chapter_title?: string
  book_subjects?: string
  book_description?: string
  book_language?: string
  chapter_id?: string
  paragraph_ids?: string[]
  // Shared
  text?: string
  description?: string
  // Catch-all for any future fields
  [key: string]: unknown
}

/**
 * Frontend attachment structure.
 * Computed fields (url, thumbnail, type, title) plus all backend fields passed through.
 */
interface Attachment {
  id: string
  type: 'image' | 'document' | 'letter'
  title: string
  url: string
  thumbnail?: string
  // Backend fields (snake_case to match backend naming)
  source?: 'letter' | 'book'
  trc_url?: string
  record_id?: string
  repository?: string
  collection?: string
  creation_date?: string
  book_title?: string
  book_authors?: string
  book_publisher?: string
  book_published_date?: string
  book_isbn?: string
  chapter_title?: string
  text?: string
  description?: string
  book_subjects?: string
  book_description?: string
  book_language?: string
  chapter_id?: string
  paragraph_ids?: string[]
}

/**
 * Backend WebSocket message types
 */
interface BackendDelta {
  type: 'delta'
  delta: string
}

interface BackendFinal {
  type: 'final'
  text: string
  citations: BackendCitation[]
}

interface BackendError {
  type: 'error'
  error: string
}

interface BackendProgress {
  type: 'progress'
  progress: string
}

interface BackendFactCheck {
  type: 'fact_check'
  flagged: boolean
  issues: Array<{ claim: string; explanation: string }>
}

type BackendMessage = BackendDelta | BackendFinal | BackendError | BackendProgress | BackendFactCheck

/**
 * POST /api/chat
 *
 * Proxies chat requests to the Python RAG backend via a persistent WebSocket.
 * Returns a streaming response with Server-Sent Events format.
 *
 * The WebSocket connection is reused across messages in the same session,
 * allowing the Python backend to maintain conversation context in-memory.
 *
 * Request body:
 * - message: string (required)
 * - sessionId: string (required, for conversation continuity)
 *
 * Response stream (SSE format):
 * - data: {"type":"delta","content":"..."}
 * - data: {"type":"final","content":"...","attachments":[...]}
 * - data: {"type":"error","error":"..."}
 * - data: [DONE]
 */
export async function POST(request: NextRequest) {
  try {
    // Parse and validate request body
    const body = await request.json()
    const validatedRequest = ChatRequestSchema.safeParse(body)

    if (!validatedRequest.success) {
      return NextResponse.json(
        { error: 'Invalid request', details: validatedRequest.error.flatten() },
        { status: 400 }
      )
    }

    const { message, sessionId, agent, mode, userId: _clientUserId } = validatedRequest.data

    if (!sessionId) {
      return NextResponse.json(
        { error: 'sessionId is required' },
        { status: 400 }
      )
    }

    // Defense-in-depth: reject if connection is already busy
    // (frontend already prevents concurrent sends via isLoading)
    if (isConnectionBusy(sessionId)) {
      return NextResponse.json(
        { error: 'A response is already in progress for this session' },
        { status: 409 }
      )
    }

    // The signed cookie — not the client-supplied userId — owns chat
    // persistence. Writes must use the same id reads will use, otherwise
    // chat-history would return nothing.
    const { userId: cookieUserId, isFresh: cookieIsFresh } = resolveUserId(request)

    // Build WebSocket URL with chat_id for session continuity. The auth token
    // is sent as an Authorization header on the handshake, not in the URL,
    // so it never lands in upstream access logs.
    const wsUrl = new URL('/ws/chat', env.PYTHON_RAG_API_URL)
    wsUrl.protocol = wsUrl.protocol.replace('http', 'ws')
    wsUrl.searchParams.set('chat_id', sessionId)

    logger.info('Processing chat message', {
      sessionId,
      wsUrl: wsUrl.toString(),
    })

    // Create a streaming response
    const stream = new ReadableStream({
      async start(controller) {
        const encoder = new TextEncoder()
        let streamClosed = false

        // Helper to safely send SSE-formatted data
        const sendEvent = (data: unknown) => {
          if (streamClosed) return
          try {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
          } catch {
            // Controller already closed
            streamClosed = true
          }
        }

        // Helper to safely close the stream
        const closeStream = () => {
          if (streamClosed) return
          streamClosed = true
          try {
            controller.close()
          } catch {
            // Already closed
          }
        }

        // Helper to send error and close
        const sendError = (error: string) => {
          sendEvent({ type: 'error', error })
          sendEvent('[DONE]')
          closeStream()
        }

        try {
          // Get or create persistent connection
          const conn = await getConnection(sessionId, wsUrl.toString(), env.RAG_API_KEY)

          // Track accumulated text for final message
          let accumulatedText = ''

          // Build JSON payload for backend (message + chat_id + agent).
          // user_id comes from the signed cookie, never the request body.
          const wsPayload = JSON.stringify({
            message,
            chat_id: sessionId,
            ...(agent && { agent }),
            ...(mode && { mode }),
            user_id: cookieUserId,
          })

          // Send message and attach per-request handlers
          const detachHandlers = await sendOnConnection(conn, wsPayload, {
            onMessage: (messageStr: string) => {
              // Check for end marker — close SSE but keep WebSocket open
              // Handles both plain-text [END] and JSON-encoded "[END]"
              if (isEndMarker(messageStr)) {
                sendEvent('[DONE]')
                closeStream()
                markIdle(conn)
                return
              }

              try {
                const parsed: BackendMessage = JSON.parse(messageStr)

                if (parsed.type === 'delta') {
                  accumulatedText += parsed.delta
                  sendEvent({
                    type: 'delta',
                    content: parsed.delta,
                  })
                } else if (parsed.type === 'final') {
                  const attachments = transformCitations(parsed.citations || [])
                  sendEvent({
                    type: 'final',
                    content: parsed.text || accumulatedText,
                    attachments,
                  })
                } else if (parsed.type === 'progress') {
                  sendEvent({
                    type: 'progress',
                    progress: parsed.progress,
                  })
                } else if (parsed.type === 'fact_check') {
                  sendEvent({
                    type: 'fact_check',
                    flagged: parsed.flagged,
                    issues: parsed.issues,
                  })
                } else if (parsed.type === 'error') {
                  // Agent error — query failed but connection is fine
                  // Send as 'agent_error' so client can distinguish from critical failures
                  sendEvent({ type: 'agent_error', error: parsed.error })
                  sendEvent('[DONE]')
                  closeStream()
                  markIdle(conn)
                }
              } catch (parseError) {
                logger.warn('Failed to parse WebSocket message', {
                  message: messageStr,
                  error: parseError instanceof Error ? parseError.message : 'Unknown',
                })
              }
            },
            onError: (error: Error) => {
              logger.error('WebSocket error during request', { error: error.message })
              sendError('Connection error. Please try again.')
            },
            onClose: (_code: number, reason: string) => {
              logger.info('WebSocket closed during request', { reason })
              sendEvent({ type: 'error', error: 'Connection to chat service was lost. Please try again.' })
              sendEvent('[DONE]')
              closeStream()
              markIdle(conn)
            },
          })

          // If client disconnects (navigates away, aborts fetch): detach handlers
          // and let the drain logic silently consume remaining messages
          const onAbort = () => {
            logger.info('Client disconnected, detaching handlers', { sessionId })
            detachHandlers()
            closeStream()
          }
          request.signal.addEventListener('abort', onAbort)
        } catch (wsError) {
          logger.error('Failed to establish WebSocket connection', {
            error: wsError instanceof Error ? wsError.message : 'Unknown',
          })
          sendError('Failed to connect to chat service. Please try again.')
        }
      },
    })

    const sseResponse = new NextResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      },
    })
    if (cookieIsFresh) setUserIdCookie(sseResponse, cookieUserId)
    return sseResponse
  } catch (error) {
    logger.error('Chat API error', {
      error: error instanceof Error ? error.message : 'Unknown',
    })

    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
