// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { render, screen, fireEvent } from '@testing-library/react'
import { Chip } from '../Chip/Chip'

describe('Chip', () => {
  it('renders label', () => {
    render(<Chip label="Test Chip" />)
    expect(screen.getByRole('button', { name: 'Test Chip' })).toBeInTheDocument()
  })

  it('renders children instead of label when provided', () => {
    render(<Chip label="Label"><span>Custom Content</span></Chip>)
    expect(screen.getByText('Custom Content')).toBeInTheDocument()
    expect(screen.queryByText('Label')).not.toBeInTheDocument()
  })

  it('handles click events', () => {
    const handleClick = jest.fn()
    render(<Chip label="Clickable" onClick={handleClick} />)

    fireEvent.click(screen.getByRole('button'))

    expect(handleClick).toHaveBeenCalledTimes(1)
  })

  it('applies selected state', () => {
    const { rerender } = render(<Chip label="Chip" selected={false} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button')).not.toHaveClass('selected')

    rerender(<Chip label="Chip" selected={true} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button')).toHaveClass('selected')
  })

  it('applies variant classes', () => {
    const { rerender } = render(<Chip label="Chip" variant="default" />)
    expect(screen.getByRole('button')).not.toHaveClass('dark')
    expect(screen.getByRole('button')).not.toHaveClass('muted')

    rerender(<Chip label="Chip" variant="dark" />)
    expect(screen.getByRole('button')).toHaveClass('dark')

    rerender(<Chip label="Chip" variant="muted" />)
    expect(screen.getByRole('button')).toHaveClass('muted')
  })

  it('applies custom className', () => {
    render(<Chip label="Chip" className="custom-class" />)
    expect(screen.getByRole('button')).toHaveClass('custom-class')
  })

  it('has type="button" to prevent form submission', () => {
    render(<Chip label="Chip" />)
    expect(screen.getByRole('button')).toHaveAttribute('type', 'button')
  })

  it('supports custom tabIndex', () => {
    render(<Chip label="Chip" tabIndex={-1} />)
    expect(screen.getByRole('button')).toHaveAttribute('tabIndex', '-1')
  })

  it('defaults to tabIndex 0', () => {
    render(<Chip label="Chip" />)
    expect(screen.getByRole('button')).toHaveAttribute('tabIndex', '0')
  })

  it('defaults to unselected state', () => {
    render(<Chip label="Chip" />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'false')
  })
})
