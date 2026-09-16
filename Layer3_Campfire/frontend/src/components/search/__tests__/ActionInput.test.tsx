// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { ActionInput } from '../ActionInput'

// Mock sessionStore
const mockSetSelectedAction = jest.fn()
const mockSetSelectedPromptId = jest.fn()
let mockSelectedAction: string | null = null
let mockHasText = false

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      selectedAction: mockSelectedAction,
      setSelectedAction: mockSetSelectedAction,
      setSelectedPromptId: mockSetSelectedPromptId,
    }
    return selector(state)
  },
  selectHasBothInputs: () => mockHasText,
}))

describe('ActionInput', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSelectedAction = null
    mockHasText = false
  })

  describe('rendering', () => {
    it('renders input field', () => {
      render(<ActionInput />)

      expect(screen.getByRole('textbox', { name: 'Select an action' })).toBeInTheDocument()
    })

    it('shows placeholder text', () => {
      render(<ActionInput />)

      expect(screen.getByPlaceholderText('I want to...')).toBeInTheDocument()
    })

    it('displays current selected action', () => {
      mockSelectedAction = 'Do research'
      render(<ActionInput />)

      expect(screen.getByRole('textbox')).toHaveValue('Do research')
    })

    it('displays empty string when no action selected', () => {
      mockSelectedAction = null
      render(<ActionInput />)

      expect(screen.getByRole('textbox')).toHaveValue('')
    })
  })

  describe('interaction', () => {
    it('calls setSelectedAction when typing', () => {
      render(<ActionInput />)

      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Find images' } })

      expect(mockSetSelectedAction).toHaveBeenCalledWith('Find images')
    })

    it('clears promptId when typing', () => {
      render(<ActionInput />)

      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'test' } })

      expect(mockSetSelectedPromptId).toHaveBeenCalledWith(null)
    })

    it('sets action to null when input is cleared', () => {
      mockSelectedAction = 'Do research'
      render(<ActionInput />)

      fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } })

      expect(mockSetSelectedAction).toHaveBeenCalledWith(null)
    })

    it('calls onSubmit when Enter is pressed and both inputs have text', () => {
      mockHasText = true
      const onSubmit = jest.fn()
      render(<ActionInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

      expect(onSubmit).toHaveBeenCalled()
    })

    it('does not call onSubmit when Enter is pressed without both inputs', () => {
      mockHasText = false
      const onSubmit = jest.fn()
      render(<ActionInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when other keys are pressed', () => {
      mockHasText = true
      const onSubmit = jest.fn()
      render(<ActionInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'a' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when Shift+Enter is pressed', () => {
      mockHasText = true
      const onSubmit = jest.fn()
      render(<ActionInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', shiftKey: true })

      expect(onSubmit).not.toHaveBeenCalled()
    })
  })
})
