'use client'

import { useRef, useEffect, useCallback, RefObject } from 'react'
import { useSessionStore } from '@/stores/sessionStore'
import { MessageBubble } from './MessageBubble'
import { LoadingIndicator } from './LoadingIndicator'
import { TIMING } from '@/lib/constants'
import styles from './ChatView.module.css'

interface ChatMessageListProps {
  /** Ref to the scrollable container (layout element) for scroll detection */
  scrollContainerRef?: RefObject<HTMLDivElement | null>
  /** Navigate with page leave animation (destination URL string) */
  onNavigate?: (destination: string) => void
}

/**
 * ChatMessageList - Renders the conversation messages with auto-scroll
 * Extracted from ChatView for single responsibility
 */
export function ChatMessageList({ scrollContainerRef, onNavigate }: ChatMessageListProps) {
  const messages = useSessionStore((state) => state.messages)
  const isLoading = useSessionStore((state) => state.isLoading)
  const progressText = useSessionStore((state) => state.progressText)

  // Ref for auto-scrolling to bottom of chat
  const chatEndRef = useRef<HTMLDivElement>(null)
  // Track if user has scrolled up (to prevent auto-scroll hijacking)
  const isUserScrolledUp = useRef(false)

  // Track user scroll position to avoid hijacking scroll when user is reading
  useEffect(() => {
    const container = scrollContainerRef?.current
    if (!container) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container
      // User is "scrolled up" if more than threshold from bottom
      isUserScrolledUp.current = scrollHeight - scrollTop - clientHeight > TIMING.SCROLL_UP_THRESHOLD
    }

    container.addEventListener('scroll', handleScroll)
    return () => container.removeEventListener('scroll', handleScroll)
  }, [scrollContainerRef])

  // Get last message content for streaming scroll updates
  const lastMessage = messages[messages.length - 1]
  const lastMessageContent = lastMessage?.content ?? ''

  // Find the last assistant message ID for showing footer only on latest response
  // Track previous loading state to detect when streaming ends
  const wasLoading = useRef(false)

  // Smooth scroll to bottom when messages change or content updates (only if user is at bottom)
  useEffect(() => {
    if (chatEndRef.current && !isUserScrolledUp.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, lastMessageContent])

  // Final scroll when streaming completes - ensures final chunk is visible
  useEffect(() => {
    // Detect transition from loading to not loading (stream finished)
    if (wasLoading.current && !isLoading) {
      // Use requestAnimationFrame to ensure DOM has updated with final content
      requestAnimationFrame(() => {
        if (chatEndRef.current && !isUserScrolledUp.current) {
          chatEndRef.current.scrollIntoView({ behavior: 'smooth' })
        }
      })
    }
    wasLoading.current = isLoading
  }, [isLoading])

  // Scroll callback for streaming responses (called during typewriter animation)
  // Memoized to prevent useTypewriter interval churn from unstable reference
  const handleStreamingProgress = useCallback(() => {
    if (chatEndRef.current && !isUserScrolledUp.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [])

  return (
    <div
      className={styles.messageList}
      role="log"
      aria-label="Chat conversation"
    >
      {/* Visually hidden live region for explicit screen reader announcements */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="visually-hidden"
      >
        {isLoading && 'Theodore is typing a response...'}
      </div>
      {messages.map((message) => {
        // Skip empty assistant messages while loading — the LoadingIndicator covers this state
        if (message.role === 'assistant' && !message.content && isLoading) {
          return null
        }

        return (
        <div
          key={message.id}
          className={`${styles.messageRow} ${
            message.role === 'user' ? styles.messageRowUser : styles.messageRowAssistant
          }`}
        >
          {message.role === 'user' ? (
            // Initial query gets h1 for semantic meaning, subsequent messages get styled div
            message.isInitialQuery ? (
              <h1 className={styles.userQuery}>{message.content}</h1>
            ) : (
              <div className={styles.userQuery}>{message.content}</div>
            )
          ) : (
            <div className={styles.responseArea}>
              <MessageBubble
                message={message}
                isStreaming={isLoading && message.id === lastMessage?.id}
                onProgress={isLoading && message.id === lastMessage?.id ? handleStreamingProgress : undefined}
                onNavigate={onNavigate}
              />
            </div>
          )}
        </div>
        )
      })}
      {/* Loading indicator - show while waiting for first streaming content */}
      {isLoading && (!lastMessage || lastMessage.role === 'user' || !lastMessage.content) && (
        <div className={`${styles.messageRow} ${styles.messageRowAssistant}`}>
          <LoadingIndicator progressText={progressText} />
        </div>
      )}
      {/* Scroll target for auto-scroll */}
      <div ref={chatEndRef} />
    </div>
  )
}
