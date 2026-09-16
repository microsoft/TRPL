// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { SearchBar } from '../SearchBar'

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string; width: number; height: number }) => (
    <img src={props.src} alt={props.alt} width={props.width} height={props.height} />
  ),
}))

// Mock sessionStore
const mockSetSelectedAction = jest.fn()
const mockSetSelectedTopic = jest.fn()
const mockSetSelectedPromptId = jest.fn()
let mockSelectedAction: string | null = null
let mockSelectedTopic: string | null = null

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      selectedAction: mockSelectedAction,
      selectedTopic: mockSelectedTopic,
      setSelectedAction: mockSetSelectedAction,
      setSelectedTopic: mockSetSelectedTopic,
      setSelectedPromptId: mockSetSelectedPromptId,
    }
    return selector(state)
  },
  selectHasInput: (state: { selectedAction: string | null; selectedTopic: string | null }) =>
    Boolean(state.selectedAction?.trim() || state.selectedTopic?.trim()),
  selectHasBothInputs: (state: { selectedAction: string | null; selectedTopic: string | null }) =>
    Boolean(state.selectedAction?.trim() && state.selectedTopic?.trim()),
}))

describe('SearchBar', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSelectedAction = null
    mockSelectedTopic = null
  })

  describe('rendering', () => {
    it('renders action input', () => {
      render(<SearchBar />)

      expect(screen.getByRole('textbox', { name: 'Select an action' })).toBeInTheDocument()
    })

    it('renders topic input', () => {
      render(<SearchBar />)

      expect(screen.getByRole('textbox', { name: 'Enter a topic' })).toBeInTheDocument()
    })

    it('renders action placeholder', () => {
      render(<SearchBar />)

      expect(screen.getByPlaceholderText('I want to...')).toBeInTheDocument()
    })

    it('renders topic placeholder', () => {
      render(<SearchBar />)

      expect(screen.getByPlaceholderText('enter a topic...')).toBeInTheDocument()
    })

    it('shows "on" divider', () => {
      render(<SearchBar />)

      expect(screen.getByText('on')).toBeInTheDocument()
    })

    it('renders disabled voice button when empty', () => {
      render(<SearchBar />)

      const button = screen.getByRole('button', { name: 'Voice input (coming soon)' })
      expect(button).toBeInTheDocument()
      expect(button).toBeDisabled()
    })

    it('displays selected action', () => {
      mockSelectedAction = 'Do research'
      render(<SearchBar />)

      expect(screen.getByRole('textbox', { name: 'Select an action' })).toHaveValue('Do research')
    })

    it('displays selected topic', () => {
      mockSelectedTopic = 'Square Deal'
      render(<SearchBar />)

      expect(screen.getByRole('textbox', { name: 'Enter a topic' })).toHaveValue('Square Deal')
    })
  })

  describe('action input interaction', () => {
    it('calls setSelectedAction when action is typed', () => {
      render(<SearchBar />)

      fireEvent.change(screen.getByRole('textbox', { name: 'Select an action' }), {
        target: { value: 'Find images' },
      })

      expect(mockSetSelectedAction).toHaveBeenCalledWith('Find images')
    })

    it('clears promptId when action is changed', () => {
      render(<SearchBar />)

      fireEvent.change(screen.getByRole('textbox', { name: 'Select an action' }), {
        target: { value: 'test' },
      })

      expect(mockSetSelectedPromptId).toHaveBeenCalledWith(null)
    })

    it('sets action to null when cleared', () => {
      mockSelectedAction = 'Do research'
      render(<SearchBar />)

      fireEvent.change(screen.getByRole('textbox', { name: 'Select an action' }), {
        target: { value: '' },
      })

      expect(mockSetSelectedAction).toHaveBeenCalledWith(null)
    })
  })

  describe('topic input interaction', () => {
    it('calls setSelectedTopic when topic is typed', () => {
      render(<SearchBar />)

      fireEvent.change(screen.getByRole('textbox', { name: 'Enter a topic' }), {
        target: { value: 'Rough Riders' },
      })

      expect(mockSetSelectedTopic).toHaveBeenCalledWith('Rough Riders')
    })

    it('clears promptId when topic is changed', () => {
      render(<SearchBar />)

      fireEvent.change(screen.getByRole('textbox', { name: 'Enter a topic' }), {
        target: { value: 'test' },
      })

      expect(mockSetSelectedPromptId).toHaveBeenCalledWith(null)
    })

    it('sets topic to null when cleared', () => {
      mockSelectedTopic = 'Square Deal'
      render(<SearchBar />)

      fireEvent.change(screen.getByRole('textbox', { name: 'Enter a topic' }), {
        target: { value: '' },
      })

      expect(mockSetSelectedTopic).toHaveBeenCalledWith(null)
    })
  })

  describe('voice button', () => {
    it('is disabled when no text (voice not yet supported)', () => {
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      const button = screen.getByRole('button', { name: 'Voice input (coming soon)' })
      expect(button).toBeDisabled()
      fireEvent.click(button)

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('shows voice icon when no text', () => {
      const { container } = render(<SearchBar />)

      expect(container.querySelector('[data-icon="microphone"]')).toBeInTheDocument()
    })
  })

  describe('submit button', () => {
    it('shows submit button when action has text', () => {
      mockSelectedAction = 'Do research'
      render(<SearchBar />)

      expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument()
    })

    it('shows submit button when topic has text', () => {
      mockSelectedTopic = 'Rough Riders'
      render(<SearchBar />)

      expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument()
    })

    it('shows arrow-up icon when has text', () => {
      mockSelectedAction = 'Do research'
      const { container } = render(<SearchBar />)

      expect(container.querySelector('[data-icon="arrow-up"]')).toBeInTheDocument()
    })

    it('calls onSubmit when clicked and both inputs have text', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

      expect(onSubmit).toHaveBeenCalled()
    })

    it('does not call onSubmit when only action has text', () => {
      mockSelectedAction = 'Do research'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when only topic has text', () => {
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

      expect(onSubmit).not.toHaveBeenCalled()
    })
  })

  describe('keyboard submission', () => {
    it('calls onSubmit when Enter is pressed in action input with both inputs filled', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Select an action' }), { key: 'Enter' })

      expect(onSubmit).toHaveBeenCalled()
    })

    it('calls onSubmit when Enter is pressed in topic input with both inputs filled', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Enter a topic' }), { key: 'Enter' })

      expect(onSubmit).toHaveBeenCalled()
    })

    it('does not call onSubmit when Shift+Enter is pressed', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Enter a topic' }), { key: 'Enter', shiftKey: true })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when Enter is pressed without text', () => {
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Select an action' }), { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when Enter is pressed with only action', () => {
      mockSelectedAction = 'Do research'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Select an action' }), { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when Enter is pressed with only topic', () => {
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Enter a topic' }), { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when other keys are pressed', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<SearchBar onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Select an action' }), { key: 'a' })

      expect(onSubmit).not.toHaveBeenCalled()
    })
  })
})
