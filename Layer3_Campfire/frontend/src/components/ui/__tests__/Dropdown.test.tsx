// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { Dropdown, type DropdownOption } from '../Dropdown/Dropdown'

const mockOptions: DropdownOption[] = [
  { label: 'Option 1', value: 'opt1' },
  { label: 'Option 2', value: 'opt2' },
  { label: 'Option 3', value: 'opt3' },
]

describe('Dropdown', () => {
  it('renders trigger button with selected option label', () => {
    render(
      <Dropdown
        options={mockOptions}
        value="opt2"
        onChange={jest.fn()}
      />
    )
    expect(screen.getByRole('button')).toHaveTextContent('Option 2')
  })

  it('shows "Select..." when no option is selected', () => {
    render(
      <Dropdown
        options={mockOptions}
        value="nonexistent"
        onChange={jest.fn()}
      />
    )
    expect(screen.getByRole('button')).toHaveTextContent('Select...')
  })

  it('opens menu on trigger click', async () => {
    render(
      <Dropdown
        options={mockOptions}
        value="opt1"
        onChange={jest.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button'))

    await waitFor(() => {
      expect(screen.getByRole('menu')).toBeInTheDocument()
    })
  })

  it('displays all options in menu', async () => {
    render(
      <Dropdown
        options={mockOptions}
        value="opt1"
        onChange={jest.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button'))

    await waitFor(() => {
      expect(screen.getByRole('menuitem', { name: 'Option 1' })).toBeInTheDocument()
      expect(screen.getByRole('menuitem', { name: 'Option 2' })).toBeInTheDocument()
      expect(screen.getByRole('menuitem', { name: 'Option 3' })).toBeInTheDocument()
    })
  })

  it('calls onChange when option is clicked', async () => {
    const handleChange = jest.fn()
    render(
      <Dropdown
        options={mockOptions}
        value="opt1"
        onChange={handleChange}
      />
    )

    fireEvent.click(screen.getByRole('button'))

    await waitFor(() => {
      expect(screen.getByRole('menu')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('menuitem', { name: 'Option 2' }))

    expect(handleChange).toHaveBeenCalledWith('opt2')
  })

  it('applies custom className to trigger', () => {
    render(
      <Dropdown
        options={mockOptions}
        value="opt1"
        onChange={jest.fn()}
        className="custom-class"
      />
    )
    expect(screen.getByRole('button')).toHaveClass('custom-class')
  })

  it('supports custom tabIndex', () => {
    render(
      <Dropdown
        options={mockOptions}
        value="opt1"
        onChange={jest.fn()}
        tabIndex={-1}
      />
    )
    expect(screen.getByRole('button')).toHaveAttribute('tabIndex', '-1')
  })

  it('marks selected option in menu', async () => {
    render(
      <Dropdown
        options={mockOptions}
        value="opt2"
        onChange={jest.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button'))

    await waitFor(() => {
      const selectedItem = screen.getByRole('menuitem', { name: 'Option 2' })
      expect(selectedItem).toHaveClass('itemSelected')
    })
  })

  it('renders chevron icon', () => {
    render(
      <Dropdown
        options={mockOptions}
        value="opt1"
        onChange={jest.fn()}
      />
    )
    const chevron = document.querySelector('[data-icon="chevron-down"]')
    expect(chevron).toBeInTheDocument()
  })

  it('handles readonly options array', () => {
    const readonlyOptions = [
      { label: 'Read', value: 'read' },
    ] as const

    render(
      <Dropdown
        options={readonlyOptions}
        value="read"
        onChange={jest.fn()}
      />
    )
    expect(screen.getByRole('button')).toHaveTextContent('Read')
  })
})
