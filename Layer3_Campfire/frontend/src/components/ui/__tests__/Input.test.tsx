// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { render, screen, fireEvent } from '@testing-library/react'
import { createRef } from 'react'
import { Input } from '../Input/Input'

describe('Input', () => {
  it('renders input element', () => {
    render(<Input />)
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('renders label when provided', () => {
    render(<Input label="Email" />)
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
  })

  it('generates id from label', () => {
    render(<Input label="Email Address" />)
    expect(screen.getByRole('textbox')).toHaveAttribute('id', 'email-address')
  })

  it('uses provided id over generated one', () => {
    render(<Input label="Email" id="custom-id" />)
    expect(screen.getByRole('textbox')).toHaveAttribute('id', 'custom-id')
  })

  it('handles value changes', () => {
    const handleChange = jest.fn()
    render(<Input onChange={handleChange} />)

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'test' } })

    expect(handleChange).toHaveBeenCalledTimes(1)
  })

  it('displays error message', () => {
    render(<Input label="Email" error="Invalid email" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid email')
  })

  it('sets aria-invalid when error is present', () => {
    const { rerender } = render(<Input label="Email" />)
    expect(screen.getByRole('textbox')).toHaveAttribute('aria-invalid', 'false')

    rerender(<Input label="Email" error="Invalid" />)
    expect(screen.getByRole('textbox')).toHaveAttribute('aria-invalid', 'true')
  })

  it('links error message with aria-describedby', () => {
    render(<Input label="Email" id="email-input" error="Invalid email" />)

    const input = screen.getByRole('textbox')
    expect(input).toHaveAttribute('aria-describedby', 'email-input-error')

    const errorMessage = screen.getByRole('alert')
    expect(errorMessage).toHaveAttribute('id', 'email-input-error')
  })

  it('does not set aria-describedby when no error', () => {
    render(<Input label="Email" />)
    expect(screen.getByRole('textbox')).not.toHaveAttribute('aria-describedby')
  })

  it('applies error class when error is present', () => {
    render(<Input error="Error" />)
    expect(screen.getByRole('textbox')).toHaveClass('error')
  })

  it('applies custom className', () => {
    render(<Input className="custom-class" />)
    expect(screen.getByRole('textbox')).toHaveClass('custom-class')
  })

  it('forwards ref to input element', () => {
    const ref = createRef<HTMLInputElement>()
    render(<Input ref={ref} />)

    expect(ref.current).toBeInstanceOf(HTMLInputElement)
  })

  it('supports placeholder', () => {
    render(<Input placeholder="Enter email..." />)
    expect(screen.getByPlaceholderText('Enter email...')).toBeInTheDocument()
  })

  it('supports disabled state', () => {
    render(<Input disabled />)
    expect(screen.getByRole('textbox')).toBeDisabled()
  })

  it('supports different input types', () => {
    const { rerender } = render(<Input type="email" />)
    expect(screen.getByRole('textbox')).toHaveAttribute('type', 'email')

    rerender(<Input type="password" />)
    // Password inputs don't have textbox role
    expect(document.querySelector('input[type="password"]')).toBeInTheDocument()
  })

  it('supports required attribute', () => {
    render(<Input required />)
    expect(screen.getByRole('textbox')).toBeRequired()
  })
})
