// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import React from 'react'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { Message } from '@/schemas/chat'

// =============================================================================
// Mocks
// =============================================================================

// Mock GSAP (ChatHistorySidebar uses gsap.timeline / gsap.set / gsap.to on mount)
const mockGsapSet = jest.fn()
const mockGsapTo = jest.fn()
const mockTimelineFromTo = jest.fn().mockReturnThis()
const mockTimelineKill = jest.fn()
jest.mock('gsap', () => ({
  gsap: {
    set: (...args: unknown[]) => mockGsapSet(...args),
    to: (...args: unknown[]) => mockGsapTo(...args),
    timeline: (opts: { onComplete?: () => void } = {}) => {
      // Fire onComplete synchronously so isInitialMount flips to false
      opts.onComplete?.()
      return { fromTo: mockTimelineFromTo, kill: mockTimelineKill }
    },
  },
}))

// Reduced motion off — exercises the animated branch (still safe because gsap is mocked)
jest.mock('@/hooks/usePrefersReducedMotion', () => ({
  getPrefersReducedMotion: jest.fn(() => false),
}))

// Anonymous user id — deterministic so the queryKey is stable
jest.mock('@/lib/anonymousUser', () => ({
  getOrCreateAnonymousUserId: () => 'test-user',
}))

// Loading screen / toast — render-null stubs
jest.mock('@/components/common/ChatLoadingScreen', () => ({
  ChatLoadingScreen: () => null,
}))
const mockShowChatHistoryError = jest.fn()
jest.mock('@/lib/toast', () => ({
  showChatHistoryError: (...args: unknown[]) => mockShowChatHistoryError(...args),
}))

// Provide a stable chat list via useChatHistory so the sidebar renders two items
jest.mock('@/hooks/useChatHistory', () => ({
  useChatHistory: () => ({
    chats: [
      { chatId: 'chat-a', title: 'Chat A' },
      { chatId: 'chat-b', title: 'Chat B' },
    ],
    isLoading: false,
    refetch: jest.fn(),
  }),
}))

// chatHistoryService.getChatMessages — controlled per-call from individual tests
const getChatMessagesMock = jest.fn()
jest.mock('@/services/chatHistory', () => ({
  chatHistoryService: {
    getChats: jest.fn().mockResolvedValue([]),
    getChatMessages: (chatId: string, signal?: AbortSignal) =>
      getChatMessagesMock(chatId, signal),
    deleteChat: jest.fn().mockResolvedValue(true),
  },
}))

// Import AFTER mocks
import { ChatHistorySidebar } from '../ChatHistorySidebar'
import { useSessionStore } from '@/stores/sessionStore'

// =============================================================================
// Helpers
// =============================================================================

const buildMessages = (chatId: string, content: string): Message[] => [
  {
    id: `${chatId}-0`,
    role: 'user',
    content,
    timestamp: new Date().toISOString(),
  },
]

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

const resetSessionStore = () => {
  act(() => {
    useSessionStore.setState({
      sessionId: null,
      messages: [],
      isLoading: false,
      selectedAction: null,
      selectedTopic: null,
      selectedPromptId: null,
      chatMode: 'discovery',
      recentChatHistoryVisibleDesktop: false,
      recentChatHistoryVisibleMobile: false,
    })
  })
}

// =============================================================================
// Tests
// =============================================================================

describe('ChatHistorySidebar rapid switch race (REPORT.md #21)', () => {
  let errorSpy: jest.SpyInstance

  beforeEach(() => {
    jest.clearAllMocks()
    resetSessionStore()
    errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    errorSpy.mockRestore()
    resetSessionStore()
  })

  it('stale chat fetch does not stomp current session', async () => {
    // Externally-controlled deferred promises per chat
    let resolveA: (m: Message[]) => void = () => {}
    let resolveB: (m: Message[]) => void = () => {}
    const promiseA = new Promise<Message[]>((res) => { resolveA = res })
    const promiseB = new Promise<Message[]>((res) => { resolveB = res })

    getChatMessagesMock.mockImplementation((chatId: string) => {
      if (chatId === 'chat-a') return promiseA
      if (chatId === 'chat-b') return promiseB
      return Promise.resolve([])
    })

    const messagesA = buildMessages('chat-a', 'Message from A')
    const messagesB = buildMessages('chat-b', 'Message from B')

    render(
      <ChatHistorySidebar isOpen={true} onToggle={jest.fn()} />,
      { wrapper: createWrapper() }
    )

    const chatAButton = screen.getByRole('button', { name: /Chat A/ })
    const chatBButton = screen.getByRole('button', { name: /Chat B/ })

    // 1) Click chat A — fetch starts, hangs unresolved
    fireEvent.click(chatAButton)
    expect(getChatMessagesMock).toHaveBeenCalledWith('chat-a', expect.anything())

    // 2) Immediately click chat B — should abort A's in-flight switch
    fireEvent.click(chatBButton)
    expect(getChatMessagesMock).toHaveBeenCalledWith('chat-b', expect.anything())

    // 3) Resolve chat B FIRST — its setState should land
    await act(async () => {
      resolveB(messagesB)
      await promiseB
    })

    let state = useSessionStore.getState()
    expect(state.sessionId).toBe('chat-b')
    expect(state.messages).toEqual(messagesB)

    // 4) Now resolve A — its post-await setState must be discarded.
    // If the fix regresses (no abort gate after await), A's resolution will
    // call setState({sessionId: 'chat-a', messages: messagesA}) and clobber B.
    await act(async () => {
      resolveA(messagesA)
      await promiseA.catch(() => {})
      // flush a microtask so the awaited continuation in handleChatClick runs
      await Promise.resolve()
    })

    state = useSessionStore.getState()
    expect(state.sessionId).toBe('chat-b')
    expect(state.messages).toEqual(messagesB)
  })

  it('successful single switch loads target chat messages into store', async () => {
    // Sanity check: when there is no race, the click path still works.
    const messagesB = buildMessages('chat-b', 'Solo message from B')
    getChatMessagesMock.mockImplementation((chatId: string) =>
      chatId === 'chat-b' ? Promise.resolve(messagesB) : Promise.resolve([])
    )

    render(
      <ChatHistorySidebar isOpen={true} onToggle={jest.fn()} />,
      { wrapper: createWrapper() }
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Chat B/ }))
    })

    await waitFor(() => {
      expect(useSessionStore.getState().sessionId).toBe('chat-b')
    })
    expect(useSessionStore.getState().messages).toEqual(messagesB)
  })
})
