/**
 * @jest-environment jsdom
 */

import { render, screen, act } from '@testing-library/react'
import { LoadingIndicator } from '../LoadingIndicator'

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string; width: number; height: number; className?: string }) => (
    <img src={props.src} alt={props.alt} width={props.width} height={props.height} className={props.className} />
  ),
}))

// Mock constants
jest.mock('@/lib/constants', () => ({
  TIMING: {
    LOADING_DOT_INTERVAL: 400,
  },
}))

describe('LoadingIndicator', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  describe('rendering', () => {
    it('renders loading text', () => {
      render(<LoadingIndicator />)

      expect(screen.getByText(/Retrieving documents/)).toBeInTheDocument()
    })

    it('renders with role="status"', () => {
      render(<LoadingIndicator />)

      expect(screen.getByRole('status')).toBeInTheDocument()
    })

    it('has aria-live="polite"', () => {
      render(<LoadingIndicator />)

      expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')
    })

    it('renders loading icon', () => {
      const { container } = render(<LoadingIndicator />)

      expect(container.querySelector('[data-icon="loading"]')).toBeInTheDocument()
    })
  })

  describe('dot animation', () => {
    it('starts with one dot', () => {
      render(<LoadingIndicator />)

      expect(screen.getByText('Retrieving documents.')).toBeInTheDocument()
    })

    it('cycles to two dots after interval', () => {
      render(<LoadingIndicator />)

      act(() => {
        jest.advanceTimersByTime(400)
      })

      expect(screen.getByText('Retrieving documents..')).toBeInTheDocument()
    })

    it('cycles to three dots after two intervals', () => {
      render(<LoadingIndicator />)

      act(() => {
        jest.advanceTimersByTime(800)
      })

      expect(screen.getByText('Retrieving documents...')).toBeInTheDocument()
    })

    it('cycles back to one dot after three intervals', () => {
      render(<LoadingIndicator />)

      act(() => {
        jest.advanceTimersByTime(1200)
      })

      expect(screen.getByText('Retrieving documents.')).toBeInTheDocument()
    })

    it('clears interval on unmount', () => {
      const { unmount } = render(<LoadingIndicator />)

      const clearIntervalSpy = jest.spyOn(global, 'clearInterval')
      unmount()

      expect(clearIntervalSpy).toHaveBeenCalled()
    })
  })
})
