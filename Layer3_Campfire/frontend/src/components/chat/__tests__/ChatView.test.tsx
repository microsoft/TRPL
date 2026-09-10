/**
 * @jest-environment jsdom
 */

import React, { act } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ChatView } from '../ChatView'
import type { Message } from '@/schemas/chat'

// =============================================================================
// Mocks - consolidated for maintainability
// =============================================================================

// Mock matchMedia for useLayoutEffect animations
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

// Mock gsap to spy on layout-margin animation calls (used by ChatView's
// useLayoutEffect for the desktop sidebar). useChatViewAnimation is mocked
// separately below, so we only need to cover ChatView's direct usage here.
const mockGsapTo = jest.fn()
const mockGsapKillTweensOf = jest.fn()
const mockGsapSet = jest.fn()
jest.mock('gsap', () => ({
  __esModule: true,
  gsap: {
    to: (...args: unknown[]) => mockGsapTo(...args),
    killTweensOf: (...args: unknown[]) => mockGsapKillTweensOf(...args),
    set: (...args: unknown[]) => mockGsapSet(...args),
  },
}))

// Helper to set window.innerWidth for breakpoint tests
const setInnerWidth = (width: number) => {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: width,
  })
}

jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: Record<string, unknown>) => <img {...props} />,
}))

jest.mock('@/hooks/useIsMobile', () => ({ useIsMobile: () => false }))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

const mockTriggerLeaveAnimation = jest.fn((cb) => cb?.())
jest.mock('@/hooks/useChatViewAnimation', () => ({
  useChatViewAnimation: () => ({
    headerRef: { current: null },
    chatAreaRef: { current: null },
    chatbarRef: { current: null },
    artifactsSidebarRef: { current: null },
    backgroundOverlayRef: { current: null },
    triggerLeaveAnimation: mockTriggerLeaveAnimation,
  }),
}))

// Mock useChat hook
const mockSendMessage = jest.fn().mockResolvedValue(true)
const mockRespondToExisting = jest.fn().mockResolvedValue(true)
const mockCancelStream = jest.fn()
let mockIsLoading = false

jest.mock('@/hooks/useChat', () => ({
  useChat: () => ({
    sendMessage: mockSendMessage,
    respondToExisting: mockRespondToExisting,
    cancelStream: mockCancelStream,
    isLoading: mockIsLoading,
  }),
}))

// Store mock state
let mockMessages: Message[] = []
let mockRecentChatHistoryVisibleDesktop = true
let mockRecentChatHistoryVisibleMobile = false
let mockChatMode = 'discovery'
let mockSessionId: string | null = null

const mockActions = {
  setChatMode: jest.fn(),
  toggleRecentChatHistoryDesktop: jest.fn(),
  toggleRecentChatHistoryMobile: jest.fn(),
  setRecentChatHistoryVisibleMobile: jest.fn(),
  resetSession: jest.fn(),
}

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector?: (s: Record<string, unknown>) => unknown) => {
    const state = {
      messages: mockMessages,
      chatMode: mockChatMode,
      sessionId: mockSessionId,
      recentChatHistoryVisibleDesktop: mockRecentChatHistoryVisibleDesktop,
      recentChatHistoryVisibleMobile: mockRecentChatHistoryVisibleMobile,
      ...mockActions,
    }
    return selector ? selector(state) : state
  },
}))

jest.mock('@/lib/constants', () => ({
  CHAT_MODE_OPTIONS: [
    { label: 'Discovery Mode', value: 'discovery', icon: 'D', description: 'Lorem ipsum' },
    { label: 'Research Mode', value: 'research', icon: 'R', description: 'Lorem ipsum' },
    { label: 'For Teachers', value: 'teachers', icon: 'T', description: 'Lorem ipsum' },
    { label: 'For Students', value: 'students', icon: 'S', description: 'Lorem ipsum' },
  ],
  CHAT_SUGGESTED_PROMPTS: ['Prompt 1', 'Prompt 2'],
  TIMING: {
    TYPEWRITER_SPEED: 5,
    LOADING_DOT_INTERVAL: 400,
    SCROLL_UP_THRESHOLD: 100,
  },
  PAGE_ANIMATION: {
    SIDEBAR: {
      SLIDE_DURATION: 0.6,
      TOGGLE_FADE_DURATION: 0.3,
      CLOSED_VISIBLE_WIDTH: 130,
    },
  },
}))

// Mock the chat service so ChatView's unmount cleanup (endSession) is a no-op
jest.mock('@/services/chat', () => ({
  endSession: jest.fn(),
  sendMessageStream: jest.fn(),
}))

jest.mock('@/services/chatHistory', () => ({
  chatHistoryService: {
    getChats: jest.fn().mockResolvedValue([]),
    getChatMessages: jest.fn().mockResolvedValue([]),
    deleteChat: jest.fn().mockResolvedValue(true),
  },
}))

// Simple mock components - just enough to verify integration
jest.mock('../ChatHistorySidebar', () => ({
  ChatHistorySidebar: ({
    isOpen,
    onToggle,
    onBeforeChatSwitch
  }: {
    isOpen: boolean
    onToggle: () => void
    onBeforeChatSwitch?: () => void
  }) => (
    <div data-testid="artifacts-sidebar" data-open={isOpen}>
      <button onClick={onBeforeChatSwitch}>Before Switch</button>
      <button onClick={onToggle}>Toggle</button>
    </div>
  ),
}))

jest.mock('../ChatMessageList', () => ({
  ChatMessageList: () => <div data-testid="chat-message-list" />,
}))

jest.mock('../SuggestedPrompts', () => ({
  SuggestedPrompts: ({ onSelect }: { onSelect: (s: string) => void }) => (
    <button data-testid="suggested-prompts" onClick={() => onSelect('Suggested')}>
      Prompt
    </button>
  ),
}))

jest.mock('../InputField', () => ({
  InputField: ({ value, onChange, onSubmit, disabled }: {
    value: string; onChange: (v: string) => void; onSubmit: () => void; disabled: boolean
  }) => (
    <>
      <input data-testid="chat-input" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} />
      <button data-testid="submit-btn" onClick={onSubmit} disabled={disabled}>Send</button>
    </>
  ),
}))

jest.mock('@/components/ui', () => ({
  InlineIcon: ({ name }: { name: string }) => <svg data-icon={name} />,
  Dropdown: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <select data-testid="mode-dropdown" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="discovery">Discovery</option>
      <option value="research">Research</option>
      <option value="teachers">For Teachers</option>
      <option value="students">For Students</option>
    </select>
  ),
  Toolbar: {
    Root: ({ children }: { children: React.ReactNode }) => <div data-testid="toolbar">{children}</div>,
    Button: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
      <button onClick={onClick}>{children}</button>
    ),
  },
}))

// Mock download utilities
const mockDownloadTextFile = jest.fn()
jest.mock('@/lib/utils', () => ({
  ...jest.requireActual('@/lib/utils'),
  downloadTextFile: (...args: unknown[]) => mockDownloadTextFile(...args),
}))

// =============================================================================
// Tests
// =============================================================================

describe('ChatView', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockMessages = []
    mockIsLoading = false
    mockRecentChatHistoryVisibleDesktop = true
    mockRecentChatHistoryVisibleMobile = false
    mockChatMode = 'discovery'
    mockSessionId = null
    mockDownloadTextFile.mockClear()
    // Default viewport: desktop (tests opt in to mobile via setInnerWidth)
    setInnerWidth(1440)
  })

  it('renders all main components', () => {
    render(<ChatView />, { wrapper: createWrapper() })

    expect(screen.getByRole('main')).toBeInTheDocument()
    // There are multiple back buttons (mobile header + desktop fixed)
    expect(screen.getAllByRole('button', { name: 'Go back to home' }).length).toBeGreaterThan(0)
    expect(screen.getByTestId('artifacts-sidebar')).toBeInTheDocument()
    expect(screen.getByTestId('chat-message-list')).toBeInTheDocument()
    expect(screen.getByTestId('suggested-prompts')).toBeInTheDocument()
    expect(screen.getByTestId('chat-input')).toBeInTheDocument()
    expect(screen.getByTestId('toolbar')).toBeInTheDocument()
  })

  describe('navigation', () => {
    it('triggers leave animation and resets session on back', () => {
      render(<ChatView />, { wrapper: createWrapper() })

      // There are multiple back buttons (mobile header + desktop fixed), click the first one
      fireEvent.click(screen.getAllByRole('button', { name: 'Go back to home' })[0])

      expect(mockTriggerLeaveAnimation).toHaveBeenCalled()
      expect(mockActions.resetSession).toHaveBeenCalled()
    })
  })

  describe('artifacts sidebar', () => {
    it('reflects visibility state', () => {
      mockRecentChatHistoryVisibleDesktop = false
      render(<ChatView />, { wrapper: createWrapper() })

      expect(screen.getByTestId('artifacts-sidebar')).toHaveAttribute('data-open', 'false')
    })

    it('toggles on button click', () => {
      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText('Toggle'))

      expect(mockActions.toggleRecentChatHistoryDesktop).toHaveBeenCalled()
    })
  })

  describe('message input', () => {
    it('fills input from suggested prompt', () => {
      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByTestId('suggested-prompts'))

      expect(screen.getByTestId('chat-input')).toHaveValue('Suggested')
    })

    it('submits message via sendMessage hook', () => {
      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Hello' } })
      fireEvent.click(screen.getByTestId('submit-btn'))

      expect(mockSendMessage).toHaveBeenCalledWith('Hello')
      expect(screen.getByTestId('chat-input')).toHaveValue('')
    })

    it('does not submit empty message', () => {
      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByTestId('submit-btn'))

      expect(mockSendMessage).not.toHaveBeenCalled()
    })

    it('does not submit while loading', () => {
      mockIsLoading = true
      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Hello' } })
      fireEvent.click(screen.getByTestId('submit-btn'))

      expect(mockSendMessage).not.toHaveBeenCalled()
    })
  })

  describe('chat mode', () => {
    it('changes mode via dropdown', () => {
      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.change(screen.getByTestId('mode-dropdown'), { target: { value: 'research' } })

      expect(mockActions.setChatMode).toHaveBeenCalledWith('research')
    })
  })

  describe('auto-response', () => {
    it('responds to unanswered user message on mount', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'Hello', timestamp: new Date().toISOString() },
      ]

      render(<ChatView />, { wrapper: createWrapper() })

      expect(mockRespondToExisting).toHaveBeenCalledWith('Hello')
    })

    it('skips response when assistant already replied', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'Hello', timestamp: new Date().toISOString() },
        { id: '2', role: 'assistant', content: 'Hi', timestamp: new Date().toISOString() },
      ]

      render(<ChatView />, { wrapper: createWrapper() })

      expect(mockRespondToExisting).not.toHaveBeenCalled()
    })

    it('skips response when already loading', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'Hello', timestamp: new Date().toISOString() },
      ]
      mockIsLoading = true

      render(<ChatView />, { wrapper: createWrapper() })

      expect(mockRespondToExisting).not.toHaveBeenCalled()
    })

    it('switching session re-arms initial auto-response trigger', () => {
      // Session A ends in an unanswered user message — auto-response fires once.
      mockSessionId = 'session-a'
      mockMessages = [
        { id: 'a1', role: 'user', content: 'Question A', timestamp: new Date().toISOString() },
      ]

      const { rerender } = render(<ChatView />, { wrapper: createWrapper() })

      expect(mockRespondToExisting).toHaveBeenCalledTimes(1)
      expect(mockRespondToExisting).toHaveBeenLastCalledWith('Question A')

      // Switch to session B (different chatId, also ends in unanswered user message).
      // Before the fix, the hasTriggeredInitialResponse ref stayed `true` so the
      // auto-response was suppressed on the new session.
      mockSessionId = 'session-b'
      mockMessages = [
        { id: 'b1', role: 'user', content: 'Question B', timestamp: new Date().toISOString() },
      ]

      rerender(<ChatView />)

      expect(mockRespondToExisting).toHaveBeenCalledTimes(2)
      expect(mockRespondToExisting).toHaveBeenLastCalledWith('Question B')
    })
  })

  describe('sidebar layout animation', () => {
    it('mobile-mounted view does not skip animation on first desktop toggle', () => {
      // Start on mobile (<1200px). The sidebar isn't visible at this size, but
      // the useLayoutEffect still runs and used to leave isInitialMount=true.
      setInnerWidth(800)
      mockRecentChatHistoryVisibleDesktop = false

      const { rerender } = render(<ChatView />, { wrapper: createWrapper() })

      // On mobile mount the layout effect returns early without animating.
      expect(mockGsapTo).not.toHaveBeenCalled()

      // Simulate viewport resize to desktop. The resize handler fires on the
      // 'resize' event and ChatView's wasDesktop ref flips to true.
      setInnerWidth(1440)
      act(() => {
        window.dispatchEvent(new Event('resize'))
      })

      mockGsapTo.mockClear()

      // Toggle the sidebar — this changes recentChatHistoryVisibleDesktop and
      // re-runs the useLayoutEffect. With the fix, isInitialMount.current was
      // cleared on the mobile mount, so this run takes the gsap.to (animate)
      // branch rather than the "set margin without animation" jump.
      mockRecentChatHistoryVisibleDesktop = true
      rerender(<ChatView />)

      // Animation path: gsap.to was invoked with marginRight target
      expect(mockGsapTo).toHaveBeenCalled()
      const firstCallArgs = mockGsapTo.mock.calls[0]
      expect(firstCallArgs[1]).toEqual(
        expect.objectContaining({ marginRight: expect.any(Number) })
      )
    })
  })

  describe('export transcript', () => {
    it('exports transcript when clicking Export Transcript button', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'Hello', timestamp: new Date().toISOString() },
        { id: '2', role: 'assistant', content: 'Hi there!', timestamp: new Date().toISOString() },
      ]

      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText('Export Transcript'))

      expect(mockDownloadTextFile).toHaveBeenCalledTimes(1)
      expect(mockDownloadTextFile).toHaveBeenCalledWith(
        expect.stringContaining('Theodore Roosevelt Reading Room'),
        expect.stringMatching(/transcript-\d{4}-\d{2}-\d{2}\.txt/)
      )
    })

    it('includes all messages in exported transcript', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'First question', timestamp: new Date().toISOString() },
        { id: '2', role: 'assistant', content: 'First answer', timestamp: new Date().toISOString() },
        { id: '3', role: 'user', content: 'Second question', timestamp: new Date().toISOString() },
        { id: '4', role: 'assistant', content: 'Second answer', timestamp: new Date().toISOString() },
      ]

      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText('Export Transcript'))

      const exportedContent = mockDownloadTextFile.mock.calls[0][0]
      expect(exportedContent).toContain('First question')
      expect(exportedContent).toContain('First answer')
      expect(exportedContent).toContain('Second question')
      expect(exportedContent).toContain('Second answer')
    })

    it('exports empty transcript when no messages', () => {
      mockMessages = []

      render(<ChatView />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText('Export Transcript'))

      expect(mockDownloadTextFile).toHaveBeenCalledTimes(1)
      expect(mockDownloadTextFile).toHaveBeenCalledWith(
        expect.stringContaining('Theodore Roosevelt Reading Room'),
        expect.any(String)
      )
    })
  })

  describe('StrictMode compatibility', () => {
    it('triggers initial response exactly once during StrictMode double-mount', () => {
      // Regression test: StrictMode runs effects → cleanups → effects again.
      // The cancelStream cleanup fires during this cycle, but must be harmless
      // (the AbortController ref is not yet set because sendMessageStream hasn't
      // resolved). The initial response must be triggered and not duplicated.
      mockMessages = [
        { id: '1', role: 'user', content: 'Hello', timestamp: new Date().toISOString() },
      ]

      render(
        <React.StrictMode>
          <ChatView />
        </React.StrictMode>,
        { wrapper: createWrapper() }
      )

      // Initial response must be triggered
      expect(mockRespondToExisting).toHaveBeenCalledWith('Hello')
      // Must not be triggered twice (hasTriggeredInitialResponse prevents duplication)
      expect(mockRespondToExisting).toHaveBeenCalledTimes(1)
    })
  })

  describe('homepage to chat flow', () => {
    it('preserves user message from homepage after StrictMode mount cycle', () => {
      // Simulate: user composed a prompt on the homepage and submitted it.
      // ChatBox.handleSubmit() called addMessage(createUserMessage(prompt, true))
      // before navigating to /chat. The message is now in the store.
      mockMessages = [
        {
          id: '1',
          role: 'user',
          content: 'Tell me about Theodore Roosevelt',
          timestamp: new Date().toISOString(),
          isInitialQuery: true,
        },
      ]

      // Make resetSession behave like the real store — it clears all session state.
      // The no-op jest.fn() mock hid this bug.
      mockActions.resetSession.mockImplementation(() => {
        mockMessages = []
        mockIsLoading = false
      })

      render(
        <React.StrictMode>
          <ChatView />
        </React.StrictMode>,
        { wrapper: createWrapper() }
      )

      // The homepage user message must survive ChatView's mount cycle.
      // StrictMode: mount → effects → cleanup → remount → effects
      // If resetSession fires during cleanup, it wipes the messages.
      expect(mockMessages).toHaveLength(1)
      expect(mockMessages[0].content).toBe('Tell me about Theodore Roosevelt')
    })
  })
})
