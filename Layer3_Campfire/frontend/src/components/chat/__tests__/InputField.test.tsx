/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { InputField } from '../InputField'

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string; width: number; height: number }) => (
    <img src={props.src} alt={props.alt} width={props.width} height={props.height} />
  ),
}))

describe('InputField', () => {
  const defaultProps = {
    value: '',
    onChange: jest.fn(),
    onSubmit: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe('rendering', () => {
    it('renders input field', () => {
      render(<InputField {...defaultProps} />)

      expect(screen.getByRole('textbox', { name: 'Chat message input' })).toBeInTheDocument()
    })

    it('renders disabled voice button when empty', () => {
      render(<InputField {...defaultProps} />)

      const button = screen.getByRole('button', { name: 'Voice input (coming soon)' })
      expect(button).toBeInTheDocument()
      expect(button).toBeDisabled()
    })

    it('renders send button when has text', () => {
      render(<InputField {...defaultProps} value="Hello" />)

      expect(screen.getByRole('button', { name: 'Send message' })).toBeInTheDocument()
    })

    it('displays current value', () => {
      render(<InputField {...defaultProps} value="Hello world" />)

      expect(screen.getByRole('textbox')).toHaveValue('Hello world')
    })

    it('uses default placeholder', () => {
      render(<InputField {...defaultProps} />)

      expect(screen.getByPlaceholderText('Ask anything...')).toBeInTheDocument()
    })

    it('uses custom placeholder', () => {
      render(<InputField {...defaultProps} placeholder="Type here..." />)

      expect(screen.getByPlaceholderText('Type here...')).toBeInTheDocument()
    })

    it('shows voice icon when empty', () => {
      const { container } = render(<InputField {...defaultProps} />)

      expect(container.querySelector('[data-icon="microphone"]')).toBeInTheDocument()
    })

    it('shows arrow-up icon when has text', () => {
      const { container } = render(<InputField {...defaultProps} value="Hello" />)

      expect(container.querySelector('[data-icon="arrow-up"]')).toBeInTheDocument()
    })
  })

  describe('interaction', () => {
    it('calls onChange when typing', () => {
      const onChange = jest.fn()
      render(<InputField {...defaultProps} onChange={onChange} />)

      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'test' } })

      expect(onChange).toHaveBeenCalledWith('test')
    })

    it('does not trigger action when voice button clicked (disabled)', () => {
      const onSubmit = jest.fn()
      render(<InputField {...defaultProps} onSubmit={onSubmit} />)

      const button = screen.getByRole('button', { name: 'Voice input (coming soon)' })
      expect(button).toBeDisabled()
      fireEvent.click(button)

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('calls onSubmit when button clicked and has text', () => {
      const onSubmit = jest.fn()
      render(<InputField {...defaultProps} value="Hello" onSubmit={onSubmit} />)

      fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

      expect(onSubmit).toHaveBeenCalled()
    })

    it('calls onSubmit when Enter is pressed', () => {
      const onSubmit = jest.fn()
      render(<InputField {...defaultProps} onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

      expect(onSubmit).toHaveBeenCalled()
    })

    it('does not call onSubmit when Shift+Enter is pressed', () => {
      const onSubmit = jest.fn()
      render(<InputField {...defaultProps} onSubmit={onSubmit} />)

      fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', shiftKey: true })

      expect(onSubmit).not.toHaveBeenCalled()
    })
  })

  describe('disabled state', () => {
    it('keeps input enabled when disabled prop is true (allows typing while streaming)', () => {
      render(<InputField {...defaultProps} disabled={true} />)

      // Input stays enabled to allow typing while waiting for response
      expect(screen.getByRole('textbox')).not.toBeDisabled()
    })

    it('disables button when disabled prop is true', () => {
      render(<InputField {...defaultProps} disabled={true} />)

      expect(screen.getByRole('button', { name: 'Voice input (coming soon)' })).toBeDisabled()
    })

    it('blocks Enter key submission when disabled', () => {
      const onSubmit = jest.fn()
      render(<InputField {...defaultProps} onSubmit={onSubmit} disabled={true} />)

      const input = screen.getByRole('textbox')
      fireEvent.keyDown(input, { key: 'Enter' })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('enables input by default', () => {
      render(<InputField {...defaultProps} />)

      expect(screen.getByRole('textbox')).not.toBeDisabled()
    })
  })

  describe('accessibility', () => {
    it('has correct tabIndex on input', () => {
      render(<InputField {...defaultProps} />)

      expect(screen.getByRole('textbox')).toHaveAttribute('tabIndex', '0')
    })

    it('has correct tabIndex on button', () => {
      render(<InputField {...defaultProps} />)

      expect(screen.getByRole('button', { name: 'Voice input (coming soon)' })).toHaveAttribute('tabIndex', '0')
    })
  })
})
