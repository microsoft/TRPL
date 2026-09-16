// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen } from '@testing-library/react'
import { Toolbar } from '../Toolbar/Toolbar'

// Mock @base-ui/react/toolbar
jest.mock('@base-ui/react/toolbar', () => ({
  Toolbar: {
    Root: ({ children, className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
      <div role="toolbar" className={className} {...props}>
        {children}
      </div>
    ),
    Button: ({ children, className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
      <button className={className} {...props}>
        {children}
      </button>
    ),
    Separator: ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
      <div role="separator" className={className} {...props} />
    ),
    Group: ({ children, className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
      <div role="group" className={className} {...props}>
        {children}
      </div>
    ),
  },
}))

describe('Toolbar', () => {
  describe('Toolbar.Root', () => {
    it('renders children', () => {
      render(
        <Toolbar.Root>
          <span>Content</span>
        </Toolbar.Root>
      )

      expect(screen.getByText('Content')).toBeInTheDocument()
    })

    it('renders with toolbar role', () => {
      render(<Toolbar.Root>Content</Toolbar.Root>)

      expect(screen.getByRole('toolbar')).toBeInTheDocument()
    })

    it('applies custom className', () => {
      render(<Toolbar.Root className="custom-toolbar">Content</Toolbar.Root>)

      expect(screen.getByRole('toolbar')).toHaveClass('custom-toolbar')
    })

    it('applies base class', () => {
      render(<Toolbar.Root>Content</Toolbar.Root>)

      expect(screen.getByRole('toolbar')).toHaveClass('root')
    })

    it('passes additional props', () => {
      render(<Toolbar.Root aria-label="Main toolbar">Content</Toolbar.Root>)

      expect(screen.getByRole('toolbar')).toHaveAttribute('aria-label', 'Main toolbar')
    })
  })

  describe('Toolbar.Button', () => {
    it('renders children', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Button>Click me</Toolbar.Button>
        </Toolbar.Root>
      )

      expect(screen.getByRole('button', { name: 'Click me' })).toBeInTheDocument()
    })

    it('applies custom className', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Button className="custom-button">Button</Toolbar.Button>
        </Toolbar.Root>
      )

      expect(screen.getByRole('button')).toHaveClass('custom-button')
    })

    it('applies base class', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Button>Button</Toolbar.Button>
        </Toolbar.Root>
      )

      expect(screen.getByRole('button')).toHaveClass('button')
    })

    it('passes additional props', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Button disabled>Disabled</Toolbar.Button>
        </Toolbar.Root>
      )

      expect(screen.getByRole('button')).toBeDisabled()
    })
  })

  describe('Toolbar.Separator', () => {
    it('renders with separator role', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Separator />
        </Toolbar.Root>
      )

      expect(screen.getByRole('separator')).toBeInTheDocument()
    })

    it('applies custom className', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Separator className="custom-separator" />
        </Toolbar.Root>
      )

      expect(screen.getByRole('separator')).toHaveClass('custom-separator')
    })

    it('applies base class', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Separator />
        </Toolbar.Root>
      )

      expect(screen.getByRole('separator')).toHaveClass('separator')
    })
  })

  describe('Toolbar.Group', () => {
    it('renders children', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Group>
            <Toolbar.Button>Button 1</Toolbar.Button>
            <Toolbar.Button>Button 2</Toolbar.Button>
          </Toolbar.Group>
        </Toolbar.Root>
      )

      expect(screen.getByText('Button 1')).toBeInTheDocument()
      expect(screen.getByText('Button 2')).toBeInTheDocument()
    })

    it('renders with group role', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Group>
            <Toolbar.Button>Button</Toolbar.Button>
          </Toolbar.Group>
        </Toolbar.Root>
      )

      expect(screen.getByRole('group')).toBeInTheDocument()
    })

    it('applies custom className', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Group className="custom-group">
            <Toolbar.Button>Button</Toolbar.Button>
          </Toolbar.Group>
        </Toolbar.Root>
      )

      expect(screen.getByRole('group')).toHaveClass('custom-group')
    })

    it('applies base class', () => {
      render(
        <Toolbar.Root>
          <Toolbar.Group>
            <Toolbar.Button>Button</Toolbar.Button>
          </Toolbar.Group>
        </Toolbar.Root>
      )

      expect(screen.getByRole('group')).toHaveClass('group')
    })
  })

  describe('composition', () => {
    it('supports full toolbar composition', () => {
      render(
        <Toolbar.Root aria-label="Formatting toolbar">
          <Toolbar.Group>
            <Toolbar.Button>Bold</Toolbar.Button>
            <Toolbar.Button>Italic</Toolbar.Button>
          </Toolbar.Group>
          <Toolbar.Separator />
          <Toolbar.Group>
            <Toolbar.Button>Align Left</Toolbar.Button>
            <Toolbar.Button>Align Center</Toolbar.Button>
          </Toolbar.Group>
        </Toolbar.Root>
      )

      expect(screen.getByRole('toolbar', { name: 'Formatting toolbar' })).toBeInTheDocument()
      expect(screen.getAllByRole('group')).toHaveLength(2)
      expect(screen.getByRole('separator')).toBeInTheDocument()
      expect(screen.getAllByRole('button')).toHaveLength(4)
    })
  })
})
