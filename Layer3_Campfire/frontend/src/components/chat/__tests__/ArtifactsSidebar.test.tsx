// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { ArtifactsSidebar } from '../ArtifactsSidebar'
import { createRef } from 'react'

// Mock next/image - spread all props to pass through aria-hidden etc.
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: Record<string, unknown>) => <img {...props} />,
}))

// Mock GSAP
const mockGsapSet = jest.fn()
const mockTimelineFromTo = jest.fn().mockReturnThis()
const mockTimelineKill = jest.fn()

jest.mock('gsap', () => ({
  gsap: {
    set: (...args: unknown[]) => mockGsapSet(...args),
    timeline: () => ({
      fromTo: mockTimelineFromTo,
      kill: mockTimelineKill,
    }),
  },
}))

// Mock usePrefersReducedMotion
jest.mock('@/hooks/usePrefersReducedMotion', () => ({
  getPrefersReducedMotion: jest.fn(() => false),
}))

// Helper to get the toggle button
const getToggleButton = () => screen.getByRole('button', { name: /hide artifacts|show artifacts/i })

describe('ArtifactsSidebar', () => {
  const defaultProps = {
    isOpen: true,
    onToggle: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe('rendering', () => {
    it('renders toggle button', () => {
      render(<ArtifactsSidebar {...defaultProps} />)

      expect(screen.getByRole('button', { name: 'Hide artifacts' })).toBeInTheDocument()
    })

    it('shows "Show Artifacts" when closed', () => {
      render(<ArtifactsSidebar {...defaultProps} isOpen={false} />)

      expect(screen.getByRole('button', { name: 'Show artifacts' })).toBeInTheDocument()
      expect(screen.getByText('Show Artifacts')).toBeInTheDocument()
    })

    it('shows "Hide Artifacts" when open', () => {
      render(<ArtifactsSidebar {...defaultProps} isOpen={true} />)

      expect(screen.getByText('Hide Artifacts')).toBeInTheDocument()
    })

    it('renders chevron icon', () => {
      const { container } = render(<ArtifactsSidebar {...defaultProps} />)

      const chevron = container.querySelector('[data-icon="chevron-down"]')
      expect(chevron).toBeInTheDocument()
    })

    it('has aria-expanded attribute', () => {
      const { rerender } = render(<ArtifactsSidebar {...defaultProps} isOpen={true} />)

      expect(getToggleButton()).toHaveAttribute('aria-expanded', 'true')

      rerender(<ArtifactsSidebar {...defaultProps} isOpen={false} />)

      expect(getToggleButton()).toHaveAttribute('aria-expanded', 'false')
    })
  })

  describe('interaction', () => {
    it('calls onToggle when button is clicked', () => {
      const onToggle = jest.fn()
      render(<ArtifactsSidebar {...defaultProps} onToggle={onToggle} />)

      fireEvent.click(getToggleButton())

      expect(onToggle).toHaveBeenCalledTimes(1)
    })
  })

  describe('animation', () => {
    beforeEach(() => {
      // Reset reduced motion mock
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { getPrefersReducedMotion } = require('@/hooks/usePrefersReducedMotion')
      getPrefersReducedMotion.mockReturnValue(false)
    })

    it('initializes animation refs', () => {
      // Verify the component renders and accepts animation-related props
      render(<ArtifactsSidebar {...defaultProps} />)
      expect(getToggleButton()).toBeInTheDocument()
    })

    it('skips animation when reduced motion is preferred', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { getPrefersReducedMotion } = require('@/hooks/usePrefersReducedMotion')
      getPrefersReducedMotion.mockReturnValue(true)

      render(<ArtifactsSidebar {...defaultProps} />)

      expect(mockGsapSet).toHaveBeenCalled()
    })

    it('renders without error on unmount', () => {
      const { unmount } = render(<ArtifactsSidebar {...defaultProps} />)

      expect(() => unmount()).not.toThrow()
    })
  })

  describe('external ref', () => {
    it('accepts external sidebarRef', () => {
      const externalRef = createRef<HTMLDivElement>()
      render(<ArtifactsSidebar {...defaultProps} sidebarRef={externalRef} />)

      // The component should use the external ref
      // We can verify the toggle button exists
      expect(getToggleButton()).toBeInTheDocument()
    })
  })

  describe('accessibility', () => {
    it('chevron image is aria-hidden (decorative)', () => {
      const { container } = render(<ArtifactsSidebar {...defaultProps} />)

      // The chevron icon inside the toggle button is aria-hidden
      const hiddenElement = container.querySelector('[aria-hidden="true"]')
      expect(hiddenElement).toBeInTheDocument()
    })

    it('has correct tabIndex on toggle button', () => {
      render(<ArtifactsSidebar {...defaultProps} />)

      expect(getToggleButton()).toHaveAttribute('tabIndex', '0')
    })
  })
})
