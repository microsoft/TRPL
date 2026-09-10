'use client'

import { useRef, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { sendMessageStream } from '@/services/chat'
import { showServerError } from '@/lib/toast'
import {
  trackChatMessage,
  trackChatResponse,
  trackChatError,
  trackSessionStart,
  trackStopResponse,
} from '@/lib/telemetry'
import { getOrCreateAnonymousUserId } from '@/lib/anonymousUser'
import { useChatHistoryStore } from '@/stores/chatHistoryStore'
import { useSessionStore, createUserMessage } from '@/stores/sessionStore'
import type { ChatRequest, Message } from '@/schemas/chat'
import { generateId } from '@/lib/utils'

/**
 * Generate a secure session ID using crypto.randomUUID()
 * Falls back to a custom implementation for older browsers
 */
function generateSessionId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  // Fallback for environments without crypto.randomUUID
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

const getChatTitle = (content: string) => {
  const trimmed = content.trim()
  if (!trimmed) return ''
  return trimmed.length > 120 ? trimmed.slice(0, 120) : trimmed
}

/**
 * Hook for chat functionality with streaming support
 * Manages streaming responses and integrates with Zustand session store
 */
export const useChat = () => {
  const isLoading = useSessionStore((state) => state.isLoading)
  const abortControllerRef = useRef<AbortController | null>(null)
  const messageStartTimeRef = useRef<number>(0)
  const queryClient = useQueryClient()
  const userId = getOrCreateAnonymousUserId()

  /**
   * Core streaming logic shared by sendMessage and respondToExisting.
   * Reads all state from the store via getState() to avoid stale closures.
   */
  const _sendAndStream = useCallback(async (
    content: string,
    opts: { addUserMessage: boolean }
  ): Promise<boolean> => {
    const store = useSessionStore.getState()

    // Block sending while streaming — read fresh from store, not closure
    if (store.isLoading) {
      console.warn('Cannot send message while previous message is still streaming')
      return false
    }

    // Generate session ID on first message
    let currentSessionId = store.sessionId
    const isNewSession = !currentSessionId
    if (!currentSessionId) {
      currentSessionId = generateSessionId()
      store.setSessionId(currentSessionId)
    }

    // Optionally add user message (sendMessage adds it, respondToExisting doesn't)
    if (isNewSession) {
      const title = getChatTitle(content)
      useChatHistoryStore.getState().addChat({
        chatId: currentSessionId,
        ...(title ? { title } : {}),
        expiresAt: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
        optimistic: true,
      })
    }

    if (opts.addUserMessage) {
      const userMessage = createUserMessage(content)
      store.addMessage(userMessage)
    }

    // Create placeholder assistant message for streaming
    const assistantMessageId = generateId()
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
    }
    store.addMessage(assistantMessage)
    store.setIsLoading(true)

    // Get current context from store
    const { selectedAction, selectedTopic, chatMode } = useSessionStore.getState()

    // Build request
    const request: ChatRequest = {
      message: content,
      sessionId: currentSessionId,
      userId,
      mode: chatMode,
      context:
        selectedAction || selectedTopic
          ? {
              action: selectedAction ?? undefined,
              topic: selectedTopic ?? undefined,
            }
          : undefined,
    }

    // Track accumulated content for delta updates
    let accumulatedContent = ''

    // Record start time for response timing (before request)
    messageStartTimeRef.current = Date.now()

    // Own the AbortController so cancelStream works even before sendMessageStream resolves
    const controller = new AbortController()
    abortControllerRef.current = controller

    try {
      await sendMessageStream(request, {
        onDelta: (delta) => {
          accumulatedContent += delta
          useSessionStore.getState().updateMessage(assistantMessageId, { content: accumulatedContent })
        },
        onFinal: (finalContent, attachments) => {
          const responseContent = finalContent || accumulatedContent
          useSessionStore.getState().updateMessage(assistantMessageId, {
            content: responseContent,
            attachments,
          })

          // Track response received telemetry
          trackChatResponse({
            responseTimeMs: Date.now() - messageStartTimeRef.current,
            contentLength: responseContent.length,
            hasAttachments: !!attachments && attachments.length > 0,
            attachmentCount: attachments?.length,
            sessionId: currentSessionId,
          })
        },
        onProgress: (progress) => {
          // Strip trailing dots - frontend animates them separately
          const cleanProgress = progress.replace(/\.+$/, '')
          useSessionStore.getState().setProgressText(cleanProgress)
        },
        onFactCheck: (flagged, issues) => {
          useSessionStore.getState().updateMessage(assistantMessageId, {
            factCheck: { flagged, issues },
          })
        },
        onAgentError: () => {
          useSessionStore.getState().setIsLoading(false)
          useSessionStore.getState().setProgressText(null)
          abortControllerRef.current = null
          useSessionStore.getState().updateMessage(assistantMessageId, {
            content: `Sorry, I'm not able to help with that request. Please try a different question.`,
          })
          // Track agent error
          trackChatError({
            errorType: 'agent_error',
            sessionId: currentSessionId,
            responseTimeMs: Date.now() - messageStartTimeRef.current,
          })
        },
        onError: () => {
          useSessionStore.getState().setIsLoading(false)
          useSessionStore.getState().setProgressText(null)
          abortControllerRef.current = null
          showServerError()
          useSessionStore.getState().updateMessage(assistantMessageId, {
            content: `Sorry, something went wrong. Please try again.`,
          })
          // Track connection/server error
          trackChatError({
            errorType: 'connection_error',
            sessionId: currentSessionId,
            responseTimeMs: Date.now() - messageStartTimeRef.current,
          })
        },
        onComplete: () => {
          useSessionStore.getState().setIsLoading(false)
          useSessionStore.getState().setProgressText(null)
          abortControllerRef.current = null

          // Refresh cached chat history only after a message completes
          queryClient.invalidateQueries({
            queryKey: ['chat-history', 'messages', userId, currentSessionId],
            exact: true,
          })
          if (isNewSession) {
            queryClient.invalidateQueries({
              queryKey: ['chat-history', 'list', userId],
              exact: true,
            })
          }
        },
      }, controller)

      // Track session start on first message
      if (isNewSession) {
        trackSessionStart({ sessionId: currentSessionId })
      }

      // Track message sent telemetry (after request starts successfully)
      trackChatMessage({
        messageLength: content.length,
        action: selectedAction ?? undefined,
        topic: selectedTopic ?? undefined,
        sessionId: currentSessionId,
      })

      return true
    } catch {
      useSessionStore.getState().setIsLoading(false)
      useSessionStore.getState().setProgressText(null)
      abortControllerRef.current = null
      showServerError()
      useSessionStore.getState().updateMessage(assistantMessageId, {
        content: `Sorry, something went wrong. Please try again.`,
      })
      return false
    }
  }, [queryClient, userId])

  /**
   * Send a message and stream the response
   * Returns false if message was blocked (e.g., already streaming)
   */
  const sendMessage = useCallback(async (content: string): Promise<boolean> => {
    return _sendAndStream(content, { addUserMessage: true })
  }, [_sendAndStream])

  /**
   * Respond to an existing user message (used when user message was already added, e.g., from homepage)
   * This only creates the assistant response without adding a user message
   */
  const respondToExisting = useCallback(async (content: string): Promise<boolean> => {
    return _sendAndStream(content, { addUserMessage: false })
  }, [_sendAndStream])

  /**
   * Cancel the current streaming request
   */
  const cancelStream = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      useSessionStore.getState().setIsLoading(false)
      useSessionStore.getState().setProgressText(null)
      trackStopResponse({
        sessionId: useSessionStore.getState().sessionId ?? undefined,
        elapsedMs: messageStartTimeRef.current
          ? Date.now() - messageStartTimeRef.current
          : 0,
      })
    }
  }, [])

  return {
    sendMessage,
    respondToExisting,
    cancelStream,
    isLoading,
  }
}
