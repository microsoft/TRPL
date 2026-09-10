'use client'

import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { Menu } from '@base-ui/react/menu'
import { gsap } from 'gsap'
import { useSessionStore } from '@/stores/sessionStore'
import { endSession } from '@/services/chat'
import { chatHistoryService } from '@/services/chatHistory'
import { useChat } from '@/hooks/useChat'
import { useChatViewAnimation } from '@/hooks/useChatViewAnimation'
import { getPrefersReducedMotion } from '@/hooks/usePrefersReducedMotion'
import { Dropdown, InlineIcon, Toolbar } from '@/components/ui'
import { ChatLoadingScreen } from '@/components/common/ChatLoadingScreen'
import { showChatHistoryError } from '@/lib/toast'
import { useChatHistoryStore } from '@/stores/chatHistoryStore'
import { useChatHistory } from '@/hooks/useChatHistory'
import { ChatHistorySidebar } from './ChatHistorySidebar'
import { ChatHistoryItem } from './ChatHistoryItem'
import { ChatMessageList } from './ChatMessageList'
import { SuggestedPrompts } from './SuggestedPrompts'
import { InputField } from './InputField'
import {
  CHAT_MODE_OPTIONS,
  CHAT_SUGGESTED_PROMPTS,
  PAGE_ANIMATION,
  type ChatMode,
} from '@/lib/constants'
import { formatTranscript, downloadTextFile } from '@/lib/utils'
import styles from './ChatView.module.css'

/**
 * ChatView - Main view component for the chat page
 * Orchestrates conversation, input controls, and artifacts sidebar
 */
export function ChatView() {
  // Local UI state (transient, component-specific)
  const [inputValue, setInputValue] = useState('')

  // Store state (shared, persisted across components)
  const messages = useSessionStore((state) => state.messages)
  const chatMode = useSessionStore((state) => state.chatMode)
  const setChatMode = useSessionStore((state) => state.setChatMode)
  const recentChatHistoryVisibleDesktop = useSessionStore(
    (state) => state.recentChatHistoryVisibleDesktop
  )
  const recentChatHistoryVisibleMobile = useSessionStore(
    (state) => state.recentChatHistoryVisibleMobile
  )
  const toggleRecentChatHistoryDesktop = useSessionStore(
    (state) => state.toggleRecentChatHistoryDesktop
  )
  const setRecentChatHistoryVisibleMobile = useSessionStore(
    (state) => state.setRecentChatHistoryVisibleMobile
  )
  const resetSession = useSessionStore((state) => state.resetSession)
  const chats = useChatHistoryStore((state) => state.chats)
  const { isLoading: isLoadingHistory } = useChatHistory()
  const cleanupExpiredChats = useChatHistoryStore((state) => state.cleanupExpiredChats)

  // Chat hook for sending messages
  const { sendMessage, respondToExisting, cancelStream, isLoading } = useChat()

  // Track whether the auto-response has fired for the current session. Tying the
  // flag to a specific sessionId (instead of "ever") lets a chat switch re-arm
  // the trigger so a loaded chat ending in an unanswered user message answers.
  const hasTriggeredInitialResponse = useRef(false)
  const triggeredForSessionRef = useRef<string | null | undefined>(undefined)

  // Loading state for mobile chat switching
  const [isLoadingChat, setIsLoadingChat] = useState<string | null>(null)

  // Animation refs and leave transition
  const {
    headerRef,
    chatAreaRef,
    chatbarRef,
    artifactsSidebarRef,
    backgroundOverlayRef,
    triggerLeaveAnimation,
  } = useChatViewAnimation()

  // Layout animation refs
  const mainRef = useRef<HTMLElement>(null)
  const layoutRef = useRef<HTMLDivElement>(null)
  const isInitialMount = useRef(true)

  // Helper to calculate sidebar width in pixels (matches ChatHistorySidebar: min(450px, 33%))
  const getSidebarWidth = () => {
    if (typeof window === 'undefined') return 450
    return Math.min(450, window.innerWidth * 0.33)
  }

  // Track previous viewport state for resize handling
  const wasDesktop = useRef<boolean | null>(null)

  // Set initial margin and animate main container when sidebar toggles - useLayoutEffect prevents flash
  // With flexbox layout, animating main's marginRight pushes both layout and chatbar together
  // Only applies to desktop (≥1200px) - mobile/tablet don't have the sidebar
  useLayoutEffect(() => {
    const isDesktop = typeof window !== 'undefined' && window.innerWidth >= 1200
    if (!isDesktop) {
      // Mark initial mount as done even on mobile — the resize handler sets the
      // margin when crossing to desktop, so the next toggle should animate.
      isInitialMount.current = false
      return
    }

    const sidebarWidth = getSidebarWidth()
    const targetMargin = recentChatHistoryVisibleDesktop
      ? sidebarWidth
      : PAGE_ANIMATION.SIDEBAR.CLOSED_VISIBLE_WIDTH

    // Set initial margin on first mount without animation
    if (isInitialMount.current) {
      isInitialMount.current = false
      if (mainRef.current) {
        mainRef.current.style.marginRight = `${targetMargin}px`
      }
      return
    }

    const reducedMotion = getPrefersReducedMotion()
    const duration = reducedMotion ? 0 : 0.4
    const ease = 'power2.inOut'

    // Animate main container margin - affects both layout and chatbar
    if (mainRef.current) {
      // Kill any existing margin animation to prevent stacking on rapid toggles
      gsap.killTweensOf(mainRef.current, 'marginRight')

      // Hide overflow on layout during animation to prevent scrollbar flash
      if (layoutRef.current) {
        layoutRef.current.style.overflow = 'hidden'
      }

      gsap.to(mainRef.current, {
        marginRight: targetMargin,
        duration,
        ease,
        onComplete: () => {
          // Restore overflow after animation completes
          if (layoutRef.current) {
            layoutRef.current.style.overflow = ''
          }
        },
      })
    }
  }, [recentChatHistoryVisibleDesktop])

  // Handle viewport resize - clear inline margin styles when crossing desktop/mobile breakpoint
  useEffect(() => {
    const handleResize = () => {
      const isDesktop = window.innerWidth >= 1200

      // Only act when crossing the breakpoint
      if (wasDesktop.current !== null && wasDesktop.current !== isDesktop) {
        if (mainRef.current) {
          if (!isDesktop) {
            // Switching to mobile/tablet: clear inline margin (CSS handles it)
            mainRef.current.style.marginRight = ''
          } else {
            // Switching to desktop: set appropriate margin based on sidebar state
            const sidebarWidth = getSidebarWidth()
            mainRef.current.style.marginRight = `${recentChatHistoryVisibleDesktop ? sidebarWidth : PAGE_ANIMATION.SIDEBAR.CLOSED_VISIBLE_WIDTH}px`
            // Mobile/tablet dropdown is rendered in a portal; force-close it on desktop.
            setRecentChatHistoryVisibleMobile(false)
          }
        }
      }

      wasDesktop.current = isDesktop
    }

    // Initialize on mount
    wasDesktop.current = window.innerWidth >= 1200

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [recentChatHistoryVisibleDesktop, setRecentChatHistoryVisibleMobile])

  const sessionId = useSessionStore((state) => state.sessionId)

  // Ref keeps latest sessionId available in cleanup without adding it to effect deps
  const sessionIdRef = useRef<string | null>(null)
  sessionIdRef.current = sessionId

  const handleBack = () => {
    // Cancel any in-progress streaming before navigating away
    cancelStream()
    // Close the persistent WebSocket connection
    if (sessionIdRef.current) endSession(sessionIdRef.current)
    // Animate out, then reset session, then navigate to home
    triggerLeaveAnimation(resetSession)
  }

  const handleBeforeChatSwitch = () => {
    cancelStream()
    if (sessionIdRef.current) endSession(sessionIdRef.current)
  }

  // Tracks the in-flight chat-switch fetch so rapid clicks can abort the previous one
  // and we discard any stale response that resolves after the user moved on.
  const chatSwitchAbortRef = useRef<AbortController | null>(null)

  const handleMobileChatClick = async (chatId: string) => {
    if (chatId === sessionId) return

    chatSwitchAbortRef.current?.abort()
    const controller = new AbortController()
    chatSwitchAbortRef.current = controller

    setIsLoadingChat(chatId)

    try {
      handleBeforeChatSwitch()
      const messages = await chatHistoryService.getChatMessages(chatId, controller.signal)
      if (controller.signal.aborted) return
      const chatEntry = useChatHistoryStore.getState().chats.find((c) => c.chatId === chatId)
      if (chatEntry?.mode) {
        setChatMode(chatEntry.mode as Parameters<typeof setChatMode>[0])
      }
      useSessionStore.setState({
        sessionId: chatId,
        messages,
        isLoading: false
      })
      setRecentChatHistoryVisibleMobile(false)
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

  // Tear down active connection on unmount (browser back button, etc.).
  // Session state is NOT reset here — HomeView owns that on mount.
  //
  // The cancel is deferred to a microtask so StrictMode's dev-mode
  // unmount→remount cycle doesn't kill the in-flight initial response. On a
  // real unmount the microtask runs to completion; on a simulated unmount the
  // remount's setup clears `isUnmountingRef` before the microtask fires.
  const isUnmountingRef = useRef(false)
  useEffect(() => {
    isUnmountingRef.current = false
    return () => {
      isUnmountingRef.current = true
      const sid = sessionIdRef.current
      queueMicrotask(() => {
        if (!isUnmountingRef.current) return
        cancelStream()
        if (sid) endSession(sid)
      })
    }
  }, [cancelStream])

  // Load chat history when mobile dropdown opens
  useEffect(() => {
    if (!recentChatHistoryVisibleMobile) return
    cleanupExpiredChats()
  }, [recentChatHistoryVisibleMobile, cleanupExpiredChats])

  // Trigger assistant response when the visible chat ends in an unanswered user message.
  // Covers: (a) message authored on the homepage and forwarded here, and
  // (b) a loaded chat whose tail is a user turn without a paired assistant reply.
  useEffect(() => {
    // Re-arm the trigger when the session actually changes so a freshly loaded
    // chat gets evaluated. Comparison against a ref (not just toggling on every
    // run) keeps StrictMode's dev mount cycle from double-firing.
    if (triggeredForSessionRef.current !== sessionId) {
      hasTriggeredInitialResponse.current = false
    }

    if (hasTriggeredInitialResponse.current) return
    if (isLoading || messages.length === 0) return

    const lastMessage = messages[messages.length - 1]
    if (lastMessage.role !== 'user') return

    hasTriggeredInitialResponse.current = true
    triggeredForSessionRef.current = sessionId
    respondToExisting(lastMessage.content)
  }, [messages, isLoading, sessionId, respondToExisting])

  const handlePromptSelect = (suggestion: string) => {
    setInputValue(suggestion)
  }

  const handleSubmit = () => {
    const trimmedInput = inputValue.trim()
    // Block if empty or already loading
    if (!trimmedInput || isLoading) {
      return
    }
    // Send message (useChat handles adding user message, assistant placeholder, and streaming)
    sendMessage(trimmedInput)
    setInputValue('')
  }

  const handleExportTranscript = () => {
    const transcript = formatTranscript(messages)
    const timestamp = new Date().toISOString().split('T')[0]
    downloadTextFile(transcript, `transcript-${timestamp}.txt`)
  }

  return (
    <>
      <ChatLoadingScreen isVisible={!!isLoadingChat} />

      {/* Artifacts sidebar - fixed position, outside main to avoid grid interference */}
      <ChatHistorySidebar
        isOpen={recentChatHistoryVisibleDesktop}
        onToggle={toggleRecentChatHistoryDesktop}
        onBeforeChatSwitch={handleBeforeChatSwitch}
        sidebarRef={artifactsSidebarRef}
      />

      <main ref={mainRef} className={styles.main}>
        {/* Header row - back button (all sizes) + artifacts toggle (mobile/tablet) */}
        <div ref={headerRef} className={styles.header}>
          <button
            className={styles.headerBackButton}
            onClick={handleBack}
            aria-label="Go back to home"
            tabIndex={0}
          >
            <InlineIcon name="arrow-left" size={40} />
            <span className={styles.headerBackLabel}>Back to Welcome</span>
          </button>
          <button
            className={`${styles.headerHistoryToggle} ${recentChatHistoryVisibleDesktop ? styles.headerHistoryToggleHidden : ''}`}
            onClick={toggleRecentChatHistoryDesktop}
            aria-label={recentChatHistoryVisibleDesktop ? 'Hide chat history' : 'Show chat history'}
            aria-expanded={recentChatHistoryVisibleDesktop}
            tabIndex={0}
          >
            <span className={styles.headerHistoryToggleIcon} aria-hidden="true">
              <InlineIcon name="panel" size={22} className={styles.headerHistoryToggleIconDefault} />
              <InlineIcon name="panel" size={22} className={styles.headerHistoryToggleIconHover} />
            </span>
          </button>
          <div className={styles.headerMobileControls}>
            <button
              className={styles.headerNewChatMobile}
              onClick={resetSession}
              aria-label="Start new chat"
              tabIndex={0}
            >
              <span>New chat</span>
              <svg
                width="10"
                height="10"
                viewBox="0 0 14 14"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M7 1V13M1 7H13"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </button>
            <Menu.Root
              open={recentChatHistoryVisibleMobile}
              onOpenChange={setRecentChatHistoryVisibleMobile}
              modal={false}
            >
              <Menu.Trigger
                className={styles.headerHistoryToggleMobile}
                tabIndex={0}
              >
              <span className={styles.headerHistoryToggleLabel}>
                Show recent chats
              </span>
              <svg
                className={styles.headerHistoryToggleChevron}
                width="8"
                height="4"
                viewBox="0 0 8 4"
                aria-hidden="true"
                focusable="false"
              >
                <path
                  d="M0.177566 0.165111C-0.0591885 0.382567 -0.0591885 0.735166 0.177566 0.952617L3.1434 3.67418C3.61699 4.10875 4.38437 4.10858 4.85772 3.67384L7.8224 0.950613C8.0592 0.733162 8.0592 0.380563 7.8224 0.163101C7.58567 -0.0543669 7.2018 -0.0543669 6.96506 0.163101L4.42759 2.49387C4.19086 2.71138 3.80699 2.71132 3.57025 2.49387L1.03491 0.165111C0.798165 -0.0523569 0.414314 -0.0523569 0.177566 0.165111Z"
                  fill="currentColor"
                />
              </svg>
            </Menu.Trigger>
            <Menu.Portal keepMounted>
              <Menu.Positioner
                className={styles.historyDropdownPositioner}
                sideOffset={6}
                side="bottom"
                align="end"
              >
                <Menu.Popup className={styles.historyDropdown}>
                  <div className={styles.historyDropdownList}>
                    {isLoadingHistory ? (
                      <div className={styles.historyDropdownMessage}>Loading chat history...</div>
                    ) : chats.length === 0 ? (
                      <div className={styles.historyDropdownMessage}>No chat history yet</div>
                    ) : (
                      chats.map((chat) => (
                        <Menu.Item
                          key={chat.chatId}
                          className={styles.historyDropdownItem}
                          onClick={() => handleMobileChatClick(chat.chatId)}
                        >
                          <ChatHistoryItem
                            chatId={chat.chatId}
                            title={
                              chat.title
                                ?? (chat.lastMessage ? `${chat.lastMessage.substring(0, 50)}...` : `Chat ${chat.chatId.slice(0, 8)}`)
                            }
                            timestamp={chat.timestamp}
                            expiresAt={chat.expiresAt}
                            isActive={chat.chatId === sessionId}
                            onClick={handleMobileChatClick}
                          />
                        </Menu.Item>
                      ))
                    )}
                  </div>
                </Menu.Popup>
              </Menu.Positioner>
            </Menu.Portal>
          </Menu.Root>
          </div>
        </div>

        <div ref={layoutRef} className={styles.layout}>
          <div ref={chatAreaRef} className={styles.chatArea}>
            <ChatMessageList scrollContainerRef={layoutRef} onNavigate={triggerLeaveAnimation} />
          </div>
        </div>

        {/* Bottom chatbar - in normal flow, flex-shrink: 0 keeps it at bottom */}
        <div ref={chatbarRef} className={styles.chatbar}>
          {/* Suggested prompts - above the white card */}
          <SuggestedPrompts
            suggestions={CHAT_SUGGESTED_PROMPTS}
            onSelect={handlePromptSelect}
            variant="dark"
          />

          <div className={styles.chatbarCard}>
            {/* Chat input with submit button */}
            <InputField
              value={inputValue}
              onChange={setInputValue}
              onSubmit={handleSubmit}
              disabled={isLoading}
            />

            {/* Action bar - toolbar with keyboard navigation */}
            <Toolbar.Root className={styles.actionBar} aria-label="Chat actions">
              <Dropdown
                options={CHAT_MODE_OPTIONS}
                value={chatMode}
                onChange={(value) => setChatMode(value as ChatMode)}
                tabIndex={0}
                animation="slide"
                disabled={messages.length > 0}
              />
              <Toolbar.Button tabIndex={0} className={styles.desktopOnly} onClick={handleExportTranscript}>Export Transcript</Toolbar.Button>
            </Toolbar.Root>
          </div>
          <p className={styles.disclaimer}>AI-generated content may be incorrect</p>
        </div>

        {/* Background overlay for smooth transition to home page */}
        <div ref={backgroundOverlayRef} className={styles.backgroundOverlay} />
      </main>
    </>
  )
}
