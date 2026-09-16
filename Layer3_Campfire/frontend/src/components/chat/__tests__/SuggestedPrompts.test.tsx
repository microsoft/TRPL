// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { SuggestedPrompts } from '../SuggestedPrompts'

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string; width: number; height: number; priority?: boolean }) => (
    <img src={props.src} alt={props.alt} width={props.width} height={props.height} />
  ),
}))

describe('SuggestedPrompts', () => {
  const suggestions = [
    'What were his key accomplishments?',
    'Tell me about conservation',
    'Describe the Rough Riders',
    'What was Square Deal?',
    'How did he become president?',
  ]

  const defaultProps = {
    suggestions,
    onSelect: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe('rendering', () => {
    it('renders suggestions as chips', () => {
      render(<SuggestedPrompts {...defaultProps} />)

      expect(screen.getByText('What were his key accomplishments?')).toBeInTheDocument()
      expect(screen.getByText('Tell me about conservation')).toBeInTheDocument()
    })

    it('renders with role="group"', () => {
      render(<SuggestedPrompts {...defaultProps} />)

      expect(screen.getByRole('group', { name: 'Suggested prompts' })).toBeInTheDocument()
    })

    it('shows only initial visible count by default', () => {
      render(<SuggestedPrompts {...defaultProps} initialVisibleCount={3} />)

      expect(screen.getByText('What were his key accomplishments?')).toBeInTheDocument()
      expect(screen.getByText('Tell me about conservation')).toBeInTheDocument()
      expect(screen.getByText('Describe the Rough Riders')).toBeInTheDocument()
      expect(screen.queryByText('What was Square Deal?')).not.toBeInTheDocument()
    })

    it('defaults to 3 visible suggestions', () => {
      render(<SuggestedPrompts {...defaultProps} />)

      // First 3 should be visible
      expect(screen.getByText('What were his key accomplishments?')).toBeInTheDocument()
      // 4th should not be visible
      expect(screen.queryByText('What was Square Deal?')).not.toBeInTheDocument()
    })

    it('shows View More button when there are more suggestions', () => {
      render(<SuggestedPrompts {...defaultProps} />)

      expect(screen.getByText('View more')).toBeInTheDocument()
    })

    it('does not show View More when all suggestions are visible', () => {
      render(<SuggestedPrompts {...defaultProps} initialVisibleCount={10} />)

      expect(screen.queryByText('View more')).not.toBeInTheDocument()
    })
  })

  describe('interaction', () => {
    it('calls onSelect when chip is clicked', () => {
      const onSelect = jest.fn()
      render(<SuggestedPrompts {...defaultProps} onSelect={onSelect} />)

      fireEvent.click(screen.getByText('What were his key accomplishments?'))

      expect(onSelect).toHaveBeenCalledWith('What were his key accomplishments?')
    })

    it('shows all suggestions when View More is clicked', () => {
      render(<SuggestedPrompts {...defaultProps} initialVisibleCount={3} />)

      fireEvent.click(screen.getByText('View more'))

      expect(screen.getByText('What was Square Deal?')).toBeInTheDocument()
      expect(screen.getByText('How did he become president?')).toBeInTheDocument()
    })

    it('hides View More button after clicking it', () => {
      render(<SuggestedPrompts {...defaultProps} />)

      fireEvent.click(screen.getByText('View more'))

      expect(screen.queryByText('View more')).not.toBeInTheDocument()
    })
  })

  describe('variants', () => {
    it('uses default variant by default', () => {
      render(<SuggestedPrompts {...defaultProps} />)

      const chip = screen.getByText('What were his key accomplishments?').closest('button')
      expect(chip).not.toHaveClass('dark')
    })

    it('applies dark variant when specified', () => {
      render(<SuggestedPrompts {...defaultProps} variant="dark" />)

      const chip = screen.getByText('What were his key accomplishments?').closest('button')
      expect(chip).toHaveClass('dark')
    })
  })

  describe('accessibility', () => {
    it('has correct tabIndex on chips', () => {
      render(<SuggestedPrompts {...defaultProps} />)

      const chip = screen.getByText('What were his key accomplishments?').closest('button')
      expect(chip).toHaveAttribute('tabIndex', '0')
    })
  })
})
