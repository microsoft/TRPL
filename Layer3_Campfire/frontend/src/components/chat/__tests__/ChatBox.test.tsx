// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { ChatBox } from '../ChatBox'

// Mock hooks
jest.mock('@/hooks/useIsMobile', () => ({
  useIsMobile: jest.fn(() => false),
}))

// Mock sessionStore
const mockAddMessage = jest.fn()
let mockComposedPrompt: string | null = null

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      addMessage: mockAddMessage,
    }
    return selector(state)
  },
  selectComposedPrompt: () => mockComposedPrompt,
  createUserMessage: (content: string, isInitial: boolean) => ({
    id: 'test-id',
    role: 'user',
    content,
    timestamp: new Date().toISOString(),
    isInitialQuery: isInitial,
  }),
}))

// Mock child components — expose onSubmit so tests can trigger submission
jest.mock('@/components/search', () => ({
  SearchBar: ({ onSubmit }: { onSubmit?: () => void }) => (
    <div data-testid="search-bar">
      <button data-testid="search-submit" onClick={onSubmit}>Submit</button>
    </div>
  ),
  ActionInput: ({ onSubmit }: { onSubmit?: () => void }) => (
    <div data-testid="action-input">
      <button data-testid="action-submit" onClick={onSubmit}>Submit</button>
    </div>
  ),
  TopicInput: ({ onSubmit }: { onSubmit?: () => void }) => (
    <div data-testid="topic-input">
      <button data-testid="topic-submit" onClick={onSubmit}>Submit</button>
    </div>
  ),
}))

jest.mock('../ActionChips', () => ({
  ActionChips: () => <div data-testid="action-chips">ActionChips</div>,
}))

jest.mock('../TopicChips', () => ({
  TopicChips: () => <div data-testid="topic-chips">TopicChips</div>,
}))

describe('ChatBox', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockComposedPrompt = null
  })

  describe('desktop layout', () => {
    beforeEach(() => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { useIsMobile } = require('@/hooks/useIsMobile')
      useIsMobile.mockReturnValue(false)
    })

    it('renders SearchBar on desktop', () => {
      render(<ChatBox />)

      expect(screen.getByTestId('search-bar')).toBeInTheDocument()
    })

    it('renders ActionChips on desktop', () => {
      render(<ChatBox />)

      expect(screen.getByTestId('action-chips')).toBeInTheDocument()
    })

    it('renders TopicChips on desktop', () => {
      render(<ChatBox />)

      expect(screen.getByTestId('topic-chips')).toBeInTheDocument()
    })

    it('does not render individual inputs on desktop', () => {
      render(<ChatBox />)

      expect(screen.queryByTestId('action-input')).not.toBeInTheDocument()
      expect(screen.queryByTestId('topic-input')).not.toBeInTheDocument()
    })
  })

  describe('mobile layout', () => {
    beforeEach(() => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { useIsMobile } = require('@/hooks/useIsMobile')
      useIsMobile.mockReturnValue(true)
    })

    it('renders ActionInput on mobile', () => {
      render(<ChatBox />)

      expect(screen.getByTestId('action-input')).toBeInTheDocument()
    })

    it('renders TopicInput on mobile', () => {
      render(<ChatBox />)

      expect(screen.getByTestId('topic-input')).toBeInTheDocument()
    })

    it('renders ActionChips on mobile', () => {
      render(<ChatBox />)

      expect(screen.getByTestId('action-chips')).toBeInTheDocument()
    })

    it('renders TopicChips on mobile', () => {
      render(<ChatBox />)

      expect(screen.getByTestId('topic-chips')).toBeInTheDocument()
    })

    it('does not render SearchBar on mobile', () => {
      render(<ChatBox />)

      expect(screen.queryByTestId('search-bar')).not.toBeInTheDocument()
    })
  })

  describe('submit handling', () => {
    beforeEach(() => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { useIsMobile } = require('@/hooks/useIsMobile')
      useIsMobile.mockReturnValue(false)
    })

    it('accepts onSubmit callback', () => {
      const onSubmit = jest.fn()
      render(<ChatBox onSubmit={onSubmit} />)

      expect(screen.getByTestId('search-bar')).toBeInTheDocument()
    })

    it('adds user message to store and triggers onSubmit when prompt exists', () => {
      mockComposedPrompt = 'Explore on Theodore Roosevelt'
      const onSubmit = jest.fn()
      render(<ChatBox onSubmit={onSubmit} />)

      fireEvent.click(screen.getByTestId('search-submit'))

      expect(mockAddMessage).toHaveBeenCalledTimes(1)
      expect(mockAddMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          role: 'user',
          content: 'Explore on Theodore Roosevelt',
          isInitialQuery: true,
        })
      )
      expect(onSubmit).toHaveBeenCalledTimes(1)
    })

    it('does not add message or trigger onSubmit when prompt is null', () => {
      mockComposedPrompt = null
      const onSubmit = jest.fn()
      render(<ChatBox onSubmit={onSubmit} />)

      fireEvent.click(screen.getByTestId('search-submit'))

      expect(mockAddMessage).not.toHaveBeenCalled()
      expect(onSubmit).not.toHaveBeenCalled()
    })
  })
})
