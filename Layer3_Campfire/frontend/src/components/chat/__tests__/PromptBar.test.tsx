/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { PromptBar } from '../PromptBar'

// Mock sessionStore
const mockSetSelectedAction = jest.fn()
const mockSetSelectedTopic = jest.fn()
const mockSetSelectedPromptId = jest.fn()
let mockSelectedPromptId: string | null = null

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      selectedPromptId: mockSelectedPromptId,
      setSelectedAction: mockSetSelectedAction,
      setSelectedTopic: mockSetSelectedTopic,
      setSelectedPromptId: mockSetSelectedPromptId,
    }
    return selector(state)
  },
}))

// Mock mockdata
jest.mock('@/mockdata', () => ({
  examplePrompts: [
    { id: 'prompt-1', text: 'Find sources on Square Deal', action: 'Do research', topic: 'Square Deal' },
    { id: 'prompt-2', text: 'Show me letters from TR', action: 'View artifacts', topic: 'Presidency' },
    { id: 'prompt-3', text: 'Find images of Rough Riders', action: 'Find images', topic: 'Rough Riders' },
    { id: 'prompt-4', text: 'Research antitrust policies', action: 'Do research', topic: 'Antitrust' },
    { id: 'prompt-5', text: 'Build a conservation lesson', action: 'Build lesson', topic: 'Conservation' },
  ],
}))

describe('PromptBar', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSelectedPromptId = null
  })

  describe('rendering', () => {
    it('renders prompt chips', () => {
      render(<PromptBar />)

      expect(screen.getByText('Find sources on Square Deal')).toBeInTheDocument()
      expect(screen.getByText('Show me letters from TR')).toBeInTheDocument()
    })

    it('renders with role="group"', () => {
      render(<PromptBar />)

      expect(screen.getByRole('group', { name: 'Example prompts' })).toBeInTheDocument()
    })

    it('shows only 3 prompts initially', () => {
      render(<PromptBar />)

      expect(screen.getByText('Find sources on Square Deal')).toBeInTheDocument()
      expect(screen.getByText('Show me letters from TR')).toBeInTheDocument()
      expect(screen.getByText('Find images of Rough Riders')).toBeInTheDocument()
      expect(screen.queryByText('Research antitrust policies')).not.toBeInTheDocument()
    })

    it('shows View More button when there are more prompts', () => {
      render(<PromptBar />)

      expect(screen.getByText('View More +')).toBeInTheDocument()
    })
  })

  describe('interaction', () => {
    it('sets action and topic when prompt is clicked', () => {
      render(<PromptBar />)

      fireEvent.click(screen.getByText('Find sources on Square Deal'))

      expect(mockSetSelectedAction).toHaveBeenCalledWith('Do research')
      expect(mockSetSelectedTopic).toHaveBeenCalledWith('Square Deal')
      expect(mockSetSelectedPromptId).toHaveBeenCalledWith('prompt-1')
    })

    it('calls onPromptSelect callback', () => {
      const onPromptSelect = jest.fn()
      render(<PromptBar onPromptSelect={onPromptSelect} />)

      fireEvent.click(screen.getByText('Find sources on Square Deal'))

      expect(onPromptSelect).toHaveBeenCalledWith('Find sources on Square Deal')
    })

    it('shows all prompts when View More is clicked', () => {
      render(<PromptBar />)

      fireEvent.click(screen.getByText('View More +'))

      expect(screen.getByText('Research antitrust policies')).toBeInTheDocument()
      expect(screen.getByText('Build a conservation lesson')).toBeInTheDocument()
    })

    it('hides View More after clicking it', () => {
      render(<PromptBar />)

      fireEvent.click(screen.getByText('View More +'))

      expect(screen.queryByText('View More +')).not.toBeInTheDocument()
    })
  })

  describe('selection state', () => {
    it('shows selected state on active prompt', () => {
      mockSelectedPromptId = 'prompt-1'
      render(<PromptBar />)

      const chip = screen.getByText('Find sources on Square Deal').closest('button')
      expect(chip).toHaveAttribute('aria-pressed', 'true')
    })

    it('does not show selected state on inactive prompts', () => {
      mockSelectedPromptId = 'prompt-1'
      render(<PromptBar />)

      const chip = screen.getByText('Show me letters from TR').closest('button')
      expect(chip).toHaveAttribute('aria-pressed', 'false')
    })
  })
})
