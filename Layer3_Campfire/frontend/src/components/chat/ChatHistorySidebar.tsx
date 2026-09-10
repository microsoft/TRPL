'use client'

import { useRef, useEffect, useLayoutEffect, RefObject, useState } from 'react'
import { gsap } from 'gsap'
import { InlineIcon } from '@/components/ui'
import { getPrefersReducedMotion } from '@/hooks/usePrefersReducedMotion'
import { ChatHistoryItem } from './ChatHistoryItem'
import { ChatLoadingScreen } from '@/components/common/ChatLoadingScreen'
import { PAGE_ANIMATION } from '@/lib/constants'
import { showChatHistoryError } from '@/lib/toast'
import { useSessionStore } from '@/stores/sessionStore'
import { useChatHistoryStore } from '@/stores/chatHistoryStore'
import { useChatHistory } from '@/hooks/useChatHistory'
import { chatHistoryService } from '@/services/chatHistory'
import { useQueryClient } from '@tanstack/react-query'
import { getOrCreateAnonymousUserId } from '@/lib/anonymousUser'
import styles from './ChatHistorySidebar.module.css'

const CHAT_TTL_MS = 24 * 60 * 60 * 1000

interface ChatHistorySidebarProps {
  isOpen: boolean
  onToggle: () => void
  /** Optional callback to cancel active streams before switching chats */
  onBeforeChatSwitch?: () => void
  /** Optional ref for parent to control leave animations */
  sidebarRef?: RefObject<HTMLDivElement | null>
}

/**
 * ChatHistorySidebar - Right side panel for displaying chat history
 * Includes the decorative ripped paper background and toggle button
 * Slides in from the right on mount, toggles via GSAP animation
 */
export function ChatHistorySidebar({
  isOpen,
  onToggle,
  onBeforeChatSwitch,
  sidebarRef: externalSidebarRef
}: ChatHistorySidebarProps) {
  const internalSidebarRef = useRef<HTMLDivElement>(null)
  // Use external ref if provided, otherwise use internal ref
  const sidebarRef = externalSidebarRef ?? internalSidebarRef
  const timelineRef = useRef<gsap.core.Timeline | null>(null)
  const hasAnimated = useRef(false)
  const isInitialMount = useRef(true)
  const initialIsOpen = useRef(isOpen)

  // Get current session ID from store
  const sessionId = useSessionStore((state) => state.sessionId)
  const resetSession = useSessionStore((state) => state.resetSession)
  const setChatMode = useSessionStore((state) => state.setChatMode)

  const { chats, isLoading: isLoadingHistory } = useChatHistory()
  const queryClient = useQueryClient()
  const userId = getOrCreateAnonymousUserId()

  // Loading state for chat switching
  const [isLoadingChat, setIsLoadingChat] = useState<string | null>(null)
  // In-flight chat-switch fetch — rapid clicks abort the previous one so stale
  // responses don't overwrite the latest chat's messages.
  const chatSwitchAbortRef = useRef<AbortController | null>(null)

  // Periodically cleanup expired chats when sidebar is open
  useEffect(() => {
    if (!isOpen) return

    const cleanupInterval = setInterval(() => {
      const cleanupExpired = useChatHistoryStore.getState().cleanupExpiredChats
      cleanupExpired()
    }, 60000) // Check every minute

    return () => clearInterval(cleanupInterval)
  }, [isOpen])

  // Helper to calculate sidebar width in pixels (matches ChatView calculation)
  const getSidebarWidth = () => {
    if (typeof window === 'undefined') return 450
    return Math.min(450, window.innerWidth * 0.33)
  }

  // Slide-in animation on mount
  useEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    if (getPrefersReducedMotion()) {
      // Skip animation - set position based on isOpen state
      if (sidebarRef.current) {
        const sidebarWidth = sidebarRef.current.offsetWidth || getSidebarWidth()
        const closedX = sidebarWidth - PAGE_ANIMATION.SIDEBAR.CLOSED_VISIBLE_WIDTH
        gsap.set(sidebarRef.current, { x: initialIsOpen.current ? 0 : closedX })
      }
      isInitialMount.current = false
      return
    }

    // Animate sidebar sliding in from right, respecting isOpen state
    const tl = gsap.timeline({
      onComplete: () => {
        isInitialMount.current = false
      }
    })
    timelineRef.current = tl

    if (sidebarRef.current) {
      const sidebarWidth = sidebarRef.current.offsetWidth || getSidebarWidth()
      const closedX = sidebarWidth - PAGE_ANIMATION.SIDEBAR.CLOSED_VISIBLE_WIDTH
      const targetX = initialIsOpen.current ? 0 : closedX

      tl.fromTo(
        sidebarRef.current,
        { x: '100%' },
        { x: targetX, duration: 0.6, ease: 'power2.out' }
      )
    }


    // Cleanup: kill timeline and reset state for React StrictMode compatibility
    return () => {
      if (timelineRef.current) {
        timelineRef.current.kill()
        timelineRef.current = null
      }
      // Reset hasAnimated so animation can re-run if component remounts (StrictMode)
      hasAnimated.current = false
      isInitialMount.current = true
    }
  }, [sidebarRef])

  // Toggle animation - slide in/out based on isOpen state
  useLayoutEffect(() => {
    // Skip toggle animation on initial mount (entrance animation handles it)
    if (isInitialMount.current) return
    if (!sidebarRef.current) return

    // Calculate how much to slide out: element width minus the visible portion
    const sidebarWidth = sidebarRef.current.offsetWidth
    const closedX = sidebarWidth - PAGE_ANIMATION.SIDEBAR.CLOSED_VISIBLE_WIDTH

    if (getPrefersReducedMotion()) {
      gsap.set(sidebarRef.current, { x: isOpen ? 0 : closedX })
      return
    }

    gsap.to(sidebarRef.current, {
      x: isOpen ? 0 : closedX,
      duration: 0.4,
      ease: 'power2.inOut'
    })
  }, [isOpen, sidebarRef])

  const handleNewChat = () => {
    // Reset session to start fresh
    resetSession()
  }

  const handleChatClick = async (chatId: string) => {
    // Don't reload if already active
    if (chatId === sessionId) return

    chatSwitchAbortRef.current?.abort()
    const controller = new AbortController()
    chatSwitchAbortRef.current = controller

    // Show loading state
    setIsLoadingChat(chatId)

    try {
      onBeforeChatSwitch?.()

      // Load messages from API
      const messages = await queryClient.fetchQuery({
        queryKey: ['chat-history', 'messages', userId, chatId],
        queryFn: ({ signal }) => chatHistoryService.getChatMessages(chatId, signal),
        staleTime: Infinity,
        gcTime: CHAT_TTL_MS
      })

      // A newer click superseded this one — discard the result.
      if (controller.signal.aborted) return

      // Restore the mode for this chat if known
      const chatEntry = useChatHistoryStore.getState().chats.find((c) => c.chatId === chatId)
      if (chatEntry?.mode) {
        setChatMode(chatEntry.mode as Parameters<typeof setChatMode>[0])
      }

      // Update session store with loaded messages
      useSessionStore.setState({
        sessionId: chatId,
        messages: messages,
        isLoading: false
      })
    } catch (error) {
      if (controller.signal.aborted) return
      console.error('Failed to load chat messages:', error)
      showChatHistoryError({ force: true })
    } finally {
      if (chatSwitchAbortRef.current === controller) {
        setIsLoadingChat(null)
        chatSwitchAbortRef.current = null
      }
    }
  }

  const handleChatHover = (chatId: string) => {
    void queryClient.prefetchQuery({
      queryKey: ['chat-history', 'messages', userId, chatId],
      queryFn: ({ signal }) => chatHistoryService.getChatMessages(chatId, signal),
      staleTime: Infinity,
      gcTime: CHAT_TTL_MS
    })
  }

  return (
    <>
      {/* Loading screen - rendered outside sidebar to cover full viewport */}
      <ChatLoadingScreen isVisible={!!isLoadingChat} />

      {/* Sidebar with ripped paper background and chat history content */}
      <div
        ref={sidebarRef}
        className={styles.sidebar}
        data-testid="artifacts-sidebar"
        data-open={isOpen}
        aria-hidden={!isOpen}
        inert={!isOpen || undefined}
      >
        <div className={styles.content}>
          <div className={styles.header}>
            <button
              className={styles.closeButton}
              onClick={onToggle}
              aria-label="Close chat history"
            >
              <span className={styles.closeButtonIcon} aria-hidden="true">
                <InlineIcon name="close" size={22} className={styles.closeButtonIconDefault} />
                <InlineIcon name="close" size={22} className={styles.closeButtonIconHover} />
              </span>
            </button>
            <h2 className={styles.title}>Chat History</h2>
            <button
              className={styles.newChatButton}
              onClick={handleNewChat}
              aria-label="Start new chat"
            >
              <span className={styles.newChatLabel}>New Chat</span>
              <div className={styles.iconContainer}>
                <InlineIcon name="plus" size={12} className={styles.plusIcon} />
              </div>
            </button>
          </div>

          <div className={styles.chatList}>
            {isLoadingHistory ? (
              <div className={styles.loadingMessage}>Loading chat history...</div>
            ) : chats.length === 0 ? (
              <div className={styles.emptyMessage}>No chat history yet.</div>
            ) : (
              chats.map((chat) => (
                <ChatHistoryItem
                  key={chat.chatId}
                  chatId={chat.chatId}
                  title={
                    chat.title
                      ?? (chat.lastMessage ? `${chat.lastMessage.substring(0, 50)}...` : `Chat ${chat.chatId.slice(0, 8)}`)
                  }
                  timestamp={chat.timestamp}
                  expiresAt={chat.expiresAt}
                  isActive={chat.chatId === sessionId}
                  onClick={handleChatClick}
                  onHover={handleChatHover}
                />
              ))
            )}
          </div>
        </div>
      </div>

    </>
  )
}
