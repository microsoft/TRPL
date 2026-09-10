/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { TopicInput } from '../TopicInput'

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string; width: number; height: number }) => (
    <img src={props.src} alt={props.alt} width={props.width} height={props.height} />
  ),
}))

// Mock sessionStore
const mockSetSelectedTopic = jest.fn()
const mockSetSelectedPromptId = jest.fn()
let mockSelectedAction: string | null = null
let mockSelectedTopic: string | null = null

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => {
    const state = {
      selectedAction: mockSelectedAction,
      selectedTopic: mockSelectedTopic,
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

describe('TopicInput', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSelectedAction = null
    mockSelectedTopic = null
  })

  describe('rendering', () => {
    it('renders input field', () => {
      render(<TopicInput />)

      expect(screen.getByRole('textbox', { name: 'Enter a topic' })).toBeInTheDocument()
    })

    it('shows placeholder text', () => {
      render(<TopicInput />)

      expect(screen.getByPlaceholderText('enter a topic...')).toBeInTheDocument()
    })

    it('shows "on" divider text', () => {
      render(<TopicInput />)

      expect(screen.getByText('on')).toBeInTheDocument()
    })

    it('renders disabled voice button when empty', () => {
      render(<TopicInput />)

      const button = screen.getByRole('button', { name: 'Voice input (coming soon)' })
      expect(button).toBeInTheDocument()
      expect(button).toBeDisabled()
    })

    it('displays current selected topic', () => {
      mockSelectedTopic = 'Square Deal'
      render(<TopicInput />)

      expect(screen.getByRole('textbox')).toHaveValue('Square Deal')
    })

    it('displays empty string when no topic selected', () => {
      mockSelectedTopic = null
      render(<TopicInput />)

      expect(screen.getByRole('textbox')).toHaveValue('')
    })
  })

  describe('interaction', () => {
    it('calls setSelectedTopic when typing', () => {
      render(<TopicInput />)

      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Rough Riders' } })

      expect(mockSetSelectedTopic).toHaveBeenCalledWith('Rough Riders')
    })

    it('clears promptId when typing', () => {
      render(<TopicInput />)

      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'test' } })

      expect(mockSetSelectedPromptId).toHaveBeenCalledWith(null)
    })

    it('sets topic to null when input is cleared', () => {
      mockSelectedTopic = 'Square Deal'
      render(<TopicInput />)

      fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } })

      expect(mockSetSelectedTopic).toHaveBeenCalledWith(null)
    })

    it('voice button is disabled when no text (voice not yet supported)', () => {
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      const button = screen.getByRole('button', { name: 'Voice input (coming soon)' })
      expect(button).toBeDisabled()
      fireEvent.click(button)

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('calls onSubmit when button is clicked and both inputs have text', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

      expect(onSubmit).toHaveBeenCalled()
    })

    it('does not call onSubmit when button is clicked with only topic', () => {
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('shows submit button when action has text', () => {
      mockSelectedAction = 'Do research'
      render(<TopicInput />)

      expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument()
    })

    it('shows arrow-up icon when has text', () => {
      mockSelectedTopic = 'Rough Riders'
      const { container } = render(<TopicInput />)

      expect(container.querySelector('[data-icon="arrow-up"]')).toBeInTheDocument()
    })

    it('shows voice icon when no text', () => {
      const { container } = render(<TopicInput />)

      expect(container.querySelector('[data-icon="microphone"]')).toBeInTheDocument()
    })
  })

  describe('keyboard submission', () => {
    it('calls onSubmit when Enter is pressed with both inputs', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

      expect(onSubmit).toHaveBeenCalled()
    })

    it('does not call onSubmit when Enter is pressed with only topic', () => {
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when Enter is pressed with only action', () => {
      mockSelectedAction = 'Do research'
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when Enter is pressed without text', () => {
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when other keys are pressed', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'a' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('does not call onSubmit when Shift+Enter is pressed', () => {
      mockSelectedAction = 'Do research'
      mockSelectedTopic = 'Rough Riders'
      const onSubmit = jest.fn()
      render(<TopicInput onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', shiftKey: true })

      expect(onSubmit).not.toHaveBeenCalled()
    })
  })
})
