/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { TopicChips } from '../TopicChips'

// Mock hooks
jest.mock('@/hooks/useIsMobile', () => ({
  useIsMobile: jest.fn(() => false),
}))

// Mock sessionStore
const mockSetSelectedTopic = jest.fn()
const mockSetSelectedPromptId = jest.fn()
let mockSelectedTopic: string | null = null

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      selectedTopic: mockSelectedTopic,
      setSelectedTopic: mockSetSelectedTopic,
      setSelectedPromptId: mockSetSelectedPromptId,
    }
    return selector(state)
  },
}))

// Mock mockdata
jest.mock('@/mockdata', () => ({
  topics: [
    { id: 'topic-1', label: "Roosevelt's presidency" },
    { id: 'topic-2', label: 'Square Deal' },
    { id: 'topic-3', label: 'Rough Riders' },
    { id: 'topic-4', label: 'Books by TR' },
    { id: 'topic-5', label: 'Antitrust' },
    { id: 'topic-6', label: 'Bull Moose' },
  ],
}))

describe('TopicChips', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSelectedTopic = null
  })

  describe('rendering', () => {
    it('renders topic chips', () => {
      render(<TopicChips />)

      expect(screen.getByText("Roosevelt's presidency")).toBeInTheDocument()
      expect(screen.getByText('Square Deal')).toBeInTheDocument()
    })

    it('renders with role="group"', () => {
      render(<TopicChips />)

      expect(screen.getByRole('group', { name: 'Topic options' })).toBeInTheDocument()
    })

    it('shows 6 chips on desktop', () => {
      render(<TopicChips />)

      const chips = screen.getAllByRole('button')
      expect(chips).toHaveLength(6)
    })

    it('shows 4 chips on mobile', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { useIsMobile } = require('@/hooks/useIsMobile')
      useIsMobile.mockReturnValue(true)

      render(<TopicChips />)

      const chips = screen.getAllByRole('button')
      expect(chips).toHaveLength(4)
    })
  })

  describe('selection', () => {
    it('calls setSelectedTopic when chip is clicked', () => {
      render(<TopicChips />)

      fireEvent.click(screen.getByText('Square Deal'))

      expect(mockSetSelectedTopic).toHaveBeenCalledWith('Square Deal')
    })

    it('clears selection when same chip is clicked again', () => {
      mockSelectedTopic = 'Square Deal'
      render(<TopicChips />)

      fireEvent.click(screen.getByText('Square Deal'))

      expect(mockSetSelectedTopic).toHaveBeenCalledWith(null)
    })

    it('clears selectedPromptId when chip is clicked', () => {
      render(<TopicChips />)

      fireEvent.click(screen.getByText('Square Deal'))

      expect(mockSetSelectedPromptId).toHaveBeenCalledWith(null)
    })

    it('shows selected chip with selected state', () => {
      mockSelectedTopic = 'Square Deal'
      render(<TopicChips />)

      const chip = screen.getByText('Square Deal').closest('button')
      expect(chip).toHaveAttribute('aria-pressed', 'true')
    })
  })
})
