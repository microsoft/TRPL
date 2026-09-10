/**
 * @jest-environment jsdom
 */

import { render, screen, act } from '@testing-library/react'
import React from 'react'

// Mock Next.js navigation
jest.mock('next/navigation', () => ({
  useRouter: () => ({
    push: jest.fn(),
  }),
}))

// Mock hooks
jest.mock('@/hooks/useIsMobile', () => ({
  useIsMobile: () => false,
}))

// Mock react-markdown (ESM module)
jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children }: { children: string }) => <>{children}</>,
}))

// Mock ResponseFooter
jest.mock('../ResponseFooter', () => ({
  ResponseFooter: () => null,
}))

import { MessageBubble } from '../MessageBubble'
import type { Message } from '@/schemas/chat'

// ============================================================================
// MessageBubble Tests
// ============================================================================

describe('MessageBubble', () => {
  const userMessage: Message = {
    id: '1',
    role: 'user',
    content: 'Hello, can you help me?',
    timestamp: new Date().toISOString(),
  }

  const assistantMessage: Message = {
    id: '2',
    role: 'assistant',
    content: 'Of course! How can I assist you today?',
    timestamp: new Date().toISOString(),
  }

  it('renders user message correctly', () => {
    render(<MessageBubble message={userMessage} />)

    expect(screen.getByText('Hello, can you help me?')).toBeInTheDocument()
  })

  it('renders assistant message correctly', () => {
    render(<MessageBubble message={assistantMessage} />)

    expect(screen.getByText('Of course! How can I assist you today?')).toBeInTheDocument()
  })

  it('applies user styling for user messages', () => {
    const { container } = render(<MessageBubble message={userMessage} />)

    const bubble = container.firstChild as HTMLElement
    expect(bubble.className).toContain('user')
  })

  it('applies assistant styling for assistant messages', () => {
    const { container } = render(<MessageBubble message={assistantMessage} />)

    const bubble = container.firstChild as HTMLElement
    expect(bubble.className).toContain('assistant')
  })

  it('renders message content as paragraph', () => {
    render(<MessageBubble message={userMessage} />)

    const paragraph = screen.getByText('Hello, can you help me?')
    expect(paragraph.tagName).toBe('P')
  })

  it('renders message with initial query flag', () => {
    const initialQuery: Message = {
      ...userMessage,
      isInitialQuery: true,
    }

    render(<MessageBubble message={initialQuery} />)
    expect(screen.getByText('Hello, can you help me?')).toBeInTheDocument()
  })
})

// ============================================================================
// Message Rendering Tests
// ============================================================================

describe('Message Rendering Edge Cases', () => {
  it('handles message with special characters', () => {
    const specialMessage: Message = {
      id: '1',
      role: 'user',
      content: 'Test <script>alert("xss")</script> message',
      timestamp: new Date().toISOString(),
    }

    render(<MessageBubble message={specialMessage} />)

    // Content should be rendered (React escapes by default)
    expect(screen.getByText(/Test.*message/)).toBeInTheDocument()
  })

  it('handles message with newlines', () => {
    const newlineMessage: Message = {
      id: '1',
      role: 'assistant',
      content: 'Line 1\nLine 2\nLine 3',
      timestamp: new Date().toISOString(),
    }

    render(<MessageBubble message={newlineMessage} />)

    expect(screen.getByText(/Line 1/)).toBeInTheDocument()
  })

  it('handles message with unicode', () => {
    const unicodeMessage: Message = {
      id: '1',
      role: 'user',
      content: 'Hello 世界 🌍 مرحبا',
      timestamp: new Date().toISOString(),
    }

    render(<MessageBubble message={unicodeMessage} />)

    expect(screen.getByText(/Hello 世界/)).toBeInTheDocument()
  })

  it('handles empty message content', () => {
    const emptyMessage: Message = {
      id: '1',
      role: 'user',
      content: '',
      timestamp: new Date().toISOString(),
    }

    render(<MessageBubble message={emptyMessage} />)

    // Should render without crashing
    const paragraph = document.querySelector('p')
    expect(paragraph).toBeInTheDocument()
  })
})

// ============================================================================
// Accessibility Tests
// ============================================================================

describe('Chat Accessibility', () => {
  const userMessage: Message = {
    id: '1',
    role: 'user',
    content: 'User question',
    timestamp: new Date().toISOString(),
  }

  const assistantMessage: Message = {
    id: '2',
    role: 'assistant',
    content: 'Assistant response',
    timestamp: new Date().toISOString(),
  }

  it('MessageBubble content is readable', () => {
    render(<MessageBubble message={userMessage} />)

    const content = screen.getByText('User question')
    expect(content).toBeVisible()
  })

  it('messages have semantic paragraph structure', () => {
    const { container } = render(
      <>
        <MessageBubble message={userMessage} />
        <MessageBubble message={assistantMessage} />
      </>
    )

    // User message uses a p tag, assistant uses markdown (mocked)
    const paragraphs = container.querySelectorAll('p')
    expect(paragraphs.length).toBeGreaterThanOrEqual(1)
  })

  it('user and assistant messages are visually distinct', () => {
    const { container } = render(
      <>
        <MessageBubble message={userMessage} />
        <MessageBubble message={assistantMessage} />
      </>
    )

    const bubbles = container.querySelectorAll('div')
    const classNames = Array.from(bubbles).map((b) => b.className)

    // Both should have distinct classes
    const hasUserClass = classNames.some((c) => c.includes('user'))
    const hasAssistantClass = classNames.some((c) => c.includes('assistant'))

    expect(hasUserClass).toBe(true)
    expect(hasAssistantClass).toBe(true)
  })
})
