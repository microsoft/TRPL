// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { ActionChips } from '../ActionChips'

// Mock sessionStore
const mockSetSelectedAction = jest.fn()
const mockSetSelectedPromptId = jest.fn()
let mockSelectedAction: string | null = null

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      selectedAction: mockSelectedAction,
      setSelectedAction: mockSetSelectedAction,
      setSelectedPromptId: mockSetSelectedPromptId,
    }
    return selector(state)
  },
}))

// Mock mockdata
jest.mock('@/mockdata', () => ({
  actions: [
    { id: 'action-1', label: 'Do research' },
    { id: 'action-2', label: 'Find images' },
    { id: 'action-3', label: 'Test knowledge' },
    { id: 'action-4', label: 'Build lesson' },
  ],
}))

describe('ActionChips', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSelectedAction = null
  })

  describe('rendering', () => {
    it('renders action chips', () => {
      render(<ActionChips />)

      expect(screen.getByText('Do research')).toBeInTheDocument()
      expect(screen.getByText('Find images')).toBeInTheDocument()
    })

    it('renders with role="group"', () => {
      render(<ActionChips />)

      expect(screen.getByRole('group', { name: 'Action options' })).toBeInTheDocument()
    })

    it('shows all chips', () => {
      render(<ActionChips />)

      const chips = screen.getAllByRole('button')
      expect(chips).toHaveLength(4)
    })
  })

  describe('selection', () => {
    it('calls setSelectedAction when chip is clicked', () => {
      render(<ActionChips />)

      fireEvent.click(screen.getByText('Do research'))

      expect(mockSetSelectedAction).toHaveBeenCalledWith('Do research')
    })

    it('clears selection when same chip is clicked again', () => {
      mockSelectedAction = 'Do research'
      render(<ActionChips />)

      fireEvent.click(screen.getByText('Do research'))

      expect(mockSetSelectedAction).toHaveBeenCalledWith(null)
    })

    it('clears selectedPromptId when chip is clicked', () => {
      render(<ActionChips />)

      fireEvent.click(screen.getByText('Do research'))

      expect(mockSetSelectedPromptId).toHaveBeenCalledWith(null)
    })

    it('shows selected chip with selected state', () => {
      mockSelectedAction = 'Do research'
      render(<ActionChips />)

      const chip = screen.getByText('Do research').closest('button')
      expect(chip).toHaveAttribute('aria-pressed', 'true')
    })
  })
})
