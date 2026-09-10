/**
 * Chat API service
 * Client-side functions for chat operations with streaming support
 */

import { API_ROUTES } from '@/lib/constants'
import { ChatRequestSchema, type ChatRequest, type Message } from '@/schemas/chat'

/**
 * Streaming event types from the API
 */
export interface ChatDeltaEvent {
  type: 'delta'
  content: string
}

export interface ChatFinalEvent {
  type: 'final'
  content: string
  attachments?: Message['attachments']
}

export interface ChatErrorEvent {
  type: 'error'
  error: string
}

export interface ChatAgentErrorEvent {
  type: 'agent_error'
  error: string
}

export interface ChatProgressEvent {
  type: 'progress'
  progress: string
}

export interface ChatFactCheckEvent {
  type: 'fact_check'
  flagged: boolean
  issues: Array<{ claim: string; explanation: string }>
}

export type ChatStreamEvent =
  | ChatDeltaEvent
  | ChatFinalEvent
  | ChatErrorEvent
  | ChatAgentErrorEvent
  | ChatProgressEvent
  | ChatFactCheckEvent

/**
 * Callbacks for streaming chat
 */
export interface StreamCallbacks {
  /** Called for each text delta received */
  onDelta?: (content: string) => void
  /** Called when the final message is received */
  onFinal?: (content: string, attachments?: Message['attachments']) => void
  /** Called when the agent fails to handle a query (non-critical) */
  onAgentError?: (error: string) => void
  /** Called on critical failures (connection lost, unreachable backend) */
  onError?: (error: string) => void
  /** Called with progress updates during processing */
  onProgress?: (progress: string) => void
  /** Called when the fact-checker returns a verdict on the final answer */
  onFactCheck?: (flagged: boolean, issues: ChatFactCheckEvent['issues']) => void
  /** Called when the stream completes */
  onComplete?: () => void
}

/**
 * Parse a Server-Sent Events line
 */
function parseSSELine(line: string): ChatStreamEvent | '[DONE]' | null {
  if (!line.startsWith('data: ')) return null

  const data = line.slice(6) // Remove 'data: ' prefix

  // Check for done marker
  if (data === '"[DONE]"' || data === '[DONE]') {
    return '[DONE]'
  }

  try {
    return JSON.parse(data) as ChatStreamEvent
  } catch {
    return null
  }
}

/**
 * Send a streaming chat message
 * Pass in an AbortController to allow cancellation; if omitted, one is created.
 * Returns the AbortController used so callers without one can still cancel.
 */
export async function sendMessageStream(
  request: ChatRequest,
  callbacks: StreamCallbacks,
  abortController: AbortController = new AbortController()
): Promise<AbortController> {
  // Validate request before sending
  const validatedRequest = ChatRequestSchema.parse(request)

  try {
    const response = await fetch(API_ROUTES.CHAT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(validatedRequest),
      signal: abortController.signal,
    })

    if (!response.ok) {
      const errorText = await response.text()
      callbacks.onError?.(`Request failed: ${response.status} ${errorText}`)
      callbacks.onComplete?.()
      return abortController
    }

    if (!response.body) {
      callbacks.onError?.('No response body received')
      callbacks.onComplete?.()
      return abortController
    }

    // Read the stream
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    const processStream = async () => {
      try {
        while (true) {
          const { done, value } = await reader.read()

          if (done) {
            callbacks.onComplete?.()
            break
          }

          // Decode the chunk and add to buffer
          buffer += decoder.decode(value, { stream: true })

          // Process complete lines
          const lines = buffer.split('\n')
          buffer = lines.pop() || '' // Keep incomplete line in buffer

          for (const line of lines) {
            const trimmedLine = line.trim()
            if (!trimmedLine) continue

            const event = parseSSELine(trimmedLine)

            if (event === '[DONE]') {
              callbacks.onComplete?.()
              return
            }

            if (event) {
              switch (event.type) {
                case 'delta':
                  callbacks.onDelta?.(event.content)
                  break
                case 'final':
                  callbacks.onFinal?.(event.content, event.attachments)
                  break
                case 'agent_error':
                  callbacks.onAgentError?.(event.error)
                  break
                case 'error':
                  callbacks.onError?.(event.error)
                  break
                case 'progress':
                  callbacks.onProgress?.(event.progress)
                  break
                case 'fact_check':
                  callbacks.onFactCheck?.(event.flagged, event.issues)
                  break
              }
            }
          }
        }
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
          // Request was cancelled, not an error
          return
        }
        callbacks.onError?.(error instanceof Error ? error.message : 'Stream read error')
        callbacks.onComplete?.()
      }
    }

    // Start processing in the background
    processStream()
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      // Request was cancelled, not an error
      return abortController
    }
    callbacks.onError?.(error instanceof Error ? error.message : 'Request failed')
    callbacks.onComplete?.()
  }

  return abortController
}

/**
 * End a chat session by closing the persistent WebSocket on the server.
 * Fire-and-forget — uses sendBeacon for reliability on page unload,
 * falls back to fetch with keepalive.
 */
export function endSession(sessionId: string): void {
  const url = API_ROUTES.SESSION_END
  const body = JSON.stringify({ sessionId })

  // sendBeacon is the most reliable way to fire on page unload
  if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
    const blob = new Blob([body], { type: 'application/json' })
    const sent = navigator.sendBeacon(url, blob)
    if (sent) return
  }

  // Fallback: fetch with keepalive (survives page navigation)
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {
    // Best-effort — nothing to do if it fails
  })
}

/**
 * Send a chat message and wait for complete response (non-streaming)
 * Useful for simpler use cases where streaming isn't needed
 */
export async function sendMessage(request: ChatRequest): Promise<{
  content: string
  attachments?: Message['attachments']
}> {
  return new Promise((resolve, reject) => {
    let fullContent = ''
    let finalAttachments: Message['attachments'] | undefined

    sendMessageStream(request, {
      onDelta: (content) => {
        fullContent += content
      },
      onFinal: (content, attachments) => {
        // Use final content if provided, otherwise use accumulated deltas
        fullContent = content || fullContent
        finalAttachments = attachments
      },
      onError: (error) => {
        reject(new Error(error))
      },
      onComplete: () => {
        resolve({
          content: fullContent,
          attachments: finalAttachments,
        })
      },
    })
  })
}
