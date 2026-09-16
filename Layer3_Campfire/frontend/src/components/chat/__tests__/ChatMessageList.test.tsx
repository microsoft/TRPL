// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen } from '@testing-library/react'
import { ChatMessageList } from '../ChatMessageList'
import type { Message } from '@/schemas/chat'

// Mock scrollIntoView
Element.prototype.scrollIntoView = jest.fn()

// Mock hooks
jest.mock('@/hooks/useIsMobile', () => ({
  useIsMobile: jest.fn(() => false),
}))

// Mock sessionStore
let mockMessages: Message[] = []
let mockIsLoading = false

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      messages: mockMessages,
      isLoading: mockIsLoading,
    }
    return selector(state)
  },
}))

// Mock constants
jest.mock('@/lib/constants', () => ({
  TIMING: {
    SCROLL_UP_THRESHOLD: 100,
  },
}))

// Mock MessageBubble
jest.mock('../MessageBubble', () => ({
  MessageBubble: ({ message, isStreaming }: { message: Message; isStreaming?: boolean }) => (
    <div data-testid="message-bubble" data-streaming={isStreaming}>
      {message.content}
    </div>
  ),
}))

// Mock LoadingIndicator
jest.mock('../LoadingIndicator', () => ({
  LoadingIndicator: () => <div data-testid="loading-indicator">Loading...</div>,
}))

describe('ChatMessageList', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockMessages = []
    mockIsLoading = false
  })

  describe('rendering', () => {
    it('renders with role="log"', () => {
      render(<ChatMessageList />)

      expect(screen.getByRole('log', { name: 'Chat conversation' })).toBeInTheDocument()
    })

    it('renders empty when no messages', () => {
      render(<ChatMessageList />)

      expect(screen.queryByTestId('message-bubble')).not.toBeInTheDocument()
    })

    it('renders messages', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'Hello', timestamp: new Date().toISOString() },
        { id: '2', role: 'assistant', content: 'Hi there', timestamp: new Date().toISOString() },
      ]

      render(<ChatMessageList />)

      expect(screen.getByText('Hello')).toBeInTheDocument()
      expect(screen.getByText('Hi there')).toBeInTheDocument()
    })

    it('renders user message as h1 when isInitialQuery', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'Initial question', timestamp: new Date().toISOString(), isInitialQuery: true },
      ]

      render(<ChatMessageList />)

      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Initial question')
    })

    it('renders user message as div when not initial query', () => {
      mockMessages = [
        { id: '1', role: 'user', content: 'Follow up', timestamp: new Date().toISOString() },
      ]

      render(<ChatMessageList />)

      const userQuery = screen.getByText('Follow up')
      expect(userQuery.tagName).toBe('DIV')
    })

    it('wraps assistant messages in MessageBubble', () => {
      mockMessages = [
        { id: '1', role: 'assistant', content: 'Response', timestamp: new Date().toISOString() },
      ]

      render(<ChatMessageList />)

      expect(screen.getByTestId('message-bubble')).toBeInTheDocument()
    })
  })

  describe('loading state', () => {
    it('shows loading indicator when isLoading and no assistant response yet', () => {
      mockIsLoading = true
      render(<ChatMessageList />)

      expect(screen.getByTestId('loading-indicator')).toBeInTheDocument()
    })

    it('hides loading indicator when not loading', () => {
      mockIsLoading = false
      render(<ChatMessageList />)

      expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument()
    })

    it('announces loading state to screen readers', () => {
      mockIsLoading = true
      render(<ChatMessageList />)

      expect(screen.getByRole('status')).toHaveTextContent('Theodore is typing a response...')
    })
  })

  describe('streaming', () => {
    it('passes isStreaming=true to last assistant message when loading', () => {
      mockMessages = [
        { id: '1', role: 'assistant', content: 'Response', timestamp: new Date().toISOString() },
      ]
      mockIsLoading = true

      render(<ChatMessageList />)

      expect(screen.getByTestId('message-bubble')).toHaveAttribute('data-streaming', 'true')
    })

    it('passes isStreaming=false when not loading', () => {
      mockMessages = [
        { id: '1', role: 'assistant', content: 'Response', timestamp: new Date().toISOString() },
      ]
      mockIsLoading = false

      render(<ChatMessageList />)

      expect(screen.getByTestId('message-bubble')).toHaveAttribute('data-streaming', 'false')
    })
  })

  describe('accessibility', () => {
    it('has visually-hidden live region', () => {
      render(<ChatMessageList />)

      const liveRegion = screen.getByRole('status')
      expect(liveRegion).toHaveAttribute('aria-live', 'polite')
      expect(liveRegion).toHaveAttribute('aria-atomic', 'true')
    })
  })
})
