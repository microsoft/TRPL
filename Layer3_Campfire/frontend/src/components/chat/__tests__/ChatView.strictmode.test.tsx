/**
 * @jest-environment jsdom
 *
 * Reproduces the dev-mode bug where the first answer from a suggested-question
 * navigation never appears. Hypothesis: the cleanup useEffect calls cancelStream
 * mid-request, aborting the in-flight fetch.
 *
 * Strategy: render ChatView with [user] in store, let respondToExisting fire,
 * then explicitly unmount and verify whether the abort controller was aborted.
 * This mimics what StrictMode's simulated unmount does in the user's dev env.
 */

import React from 'react'
import { render, waitFor, configure } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ChatView } from '../ChatView'
import { sendMessageStream } from '@/services/chat'
import { useSessionStore } from '@/stores/sessionStore'

jest.useRealTimers()

// Enable React StrictMode wrapper on render() so we faithfully reproduce the
// Next.js dev environment, where effects run setup → cleanup → setup on mount.
configure({ reactStrictMode: true })

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
})

jest.mock('gsap', () => ({
  __esModule: true,
  gsap: {
    to: jest.fn(),
    killTweensOf: jest.fn(),
    set: jest.fn(),
  },
}))

jest.mock('@/hooks/useIsMobile', () => ({ useIsMobile: () => false }))

jest.mock('@/hooks/useChatViewAnimation', () => ({
  useChatViewAnimation: () => ({
    headerRef: { current: null },
    chatAreaRef: { current: null },
    chatbarRef: { current: null },
    artifactsSidebarRef: { current: null },
    backgroundOverlayRef: { current: null },
    triggerLeaveAnimation: jest.fn((cb) => cb?.()),
  }),
}))

jest.mock('@/services/chat', () => ({
  sendMessageStream: jest.fn(),
  endSession: jest.fn(),
}))

jest.mock('@/services/chatHistory', () => ({
  chatHistoryService: {
    getChats: jest.fn().mockResolvedValue([]),
    getChatMessages: jest.fn().mockResolvedValue([]),
    deleteChat: jest.fn().mockResolvedValue(true),
  },
}))

jest.mock('../ChatHistorySidebar', () => ({
  ChatHistorySidebar: () => <div data-testid="artifacts-sidebar" />,
}))
jest.mock('../ChatMessageList', () => ({
  ChatMessageList: () => <div data-testid="chat-message-list" />,
}))
jest.mock('../SuggestedPrompts', () => ({
  SuggestedPrompts: () => <div data-testid="suggested-prompts" />,
}))
jest.mock('../InputField', () => ({
  InputField: () => <input data-testid="chat-input" />,
}))
jest.mock('@/components/ui', () => ({
  InlineIcon: ({ name }: { name: string }) => <svg data-icon={name} />,
  Dropdown: () => <select data-testid="mode-dropdown" />,
  Toolbar: {
    Root: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Button: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  },
}))

const mockSendMessageStream = sendMessageStream as jest.MockedFunction<typeof sendMessageStream>

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('ChatView — unmount-cleanup vs in-flight stream', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    useSessionStore.setState({
      sessionId: null,
      messages: [
        {
          id: 'user-1',
          role: 'user',
          content: 'Tell me about Theodore Roosevelt',
          timestamp: new Date().toISOString(),
          isInitialQuery: true,
        },
      ],
      isLoading: false,
      progressText: null,
      chatMode: 'discovery',
      recentChatHistoryVisibleDesktop: false,
      recentChatHistoryVisibleMobile: false,
      selectedAction: null,
      selectedTopic: null,
      selectedPromptId: null,
    })
  })

  it('does not abort the in-flight stream during StrictMode setup → cleanup → setup', async () => {
    // configure({ reactStrictMode: true }) wraps render() in <StrictMode>, so
    // effects double-invoke on the same component instance — exactly matching
    // Next.js dev mode behavior.
    const capturedControllers: AbortController[] = []
    mockSendMessageStream.mockImplementation(async (_req, _cbs, controller) => {
      if (controller) capturedControllers.push(controller)
      await new Promise(() => {})
      return controller!
    })

    render(<ChatView />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockSendMessageStream).toHaveBeenCalledTimes(1)
    })

    // Let any deferred cleanups (microtask/setTimeout) flush.
    await new Promise((resolve) => setTimeout(resolve, 0))

    // The cleanup fires during StrictMode's simulated unmount, but the remount
    // must restore state so the in-flight fetch is preserved.
    expect(capturedControllers[0].signal.aborted).toBe(false)
  })

  it('aborts the in-flight stream when ChatView is truly unmounted (no remount)', async () => {
    // The user navigates away (back button, etc.). We DO want to free resources.
    const capturedControllers: AbortController[] = []
    mockSendMessageStream.mockImplementation(async (_req, _cbs, controller) => {
      if (controller) capturedControllers.push(controller)
      await new Promise(() => {})
      return controller!
    })

    const { unmount } = render(<ChatView />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockSendMessageStream).toHaveBeenCalledTimes(1)
    })

    unmount()

    // Let the deferred cleanup fire.
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(capturedControllers[0].signal.aborted).toBe(true)
  })
})
