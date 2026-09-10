/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { ResponseFooter } from '../ResponseFooter'

jest.mock('next/image', () => ({
  __esModule: true,
  default: ({ src, alt, unoptimized, ...props }: Record<string, unknown>) => {
    const resolvedSrc = typeof src === 'string' ? src : ''
    return <img src={resolvedSrc} alt={alt as string | undefined} {...props} />
  },
}))

describe('ResponseFooter', () => {
  const defaultProps = {
    sourceCount: 3,
    onViewSources: jest.fn(),
    onCopy: jest.fn().mockResolvedValue(true),
    onReportIssue: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('renders all three buttons', () => {
    render(<ResponseFooter {...defaultProps} />)

    expect(screen.getByRole('button', { name: /view 3 sources/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /copy response/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /report an issue/i })).toBeInTheDocument()
  })

  it('displays correct source count', () => {
    render(<ResponseFooter {...defaultProps} sourceCount={5} />)

    expect(screen.getByText('View 5 Sources')).toBeInTheDocument()
  })

  describe('View Sources button', () => {
    it('calls onViewSources when clicked', () => {
      render(<ResponseFooter {...defaultProps} />)

      fireEvent.click(screen.getByRole('button', { name: /view 3 sources/i }))

      expect(defaultProps.onViewSources).toHaveBeenCalledTimes(1)
    })
  })

  describe('Copy button', () => {
    it('calls onCopy when clicked', async () => {
      render(<ResponseFooter {...defaultProps} />)

      fireEvent.click(screen.getByRole('button', { name: /copy response/i }))

      await waitFor(() => {
        expect(defaultProps.onCopy).toHaveBeenCalledTimes(1)
      })
    })

    it('shows checkmark after successful copy', async () => {
      render(<ResponseFooter {...defaultProps} />)

      fireEvent.click(screen.getByRole('button', { name: /copy response/i }))

      await waitFor(() => {
        expect(screen.getByText('✓')).toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: /copied/i })).toBeInTheDocument()
    })

    it('reverts to copy icon after 1 second', async () => {
      render(<ResponseFooter {...defaultProps} />)

      fireEvent.click(screen.getByRole('button', { name: /copy response/i }))
      await waitFor(() => {
        expect(screen.getByText('✓')).toBeInTheDocument()
      })

      act(() => {
        jest.advanceTimersByTime(1000)
      })

      expect(screen.queryByText('✓')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /copy response/i })).toBeInTheDocument()
    })

    it('does not show copied state when copy fails', async () => {
      render(<ResponseFooter {...defaultProps} onCopy={jest.fn().mockResolvedValue(false)} />)

      fireEvent.click(screen.getByRole('button', { name: /copy response/i }))

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /copy response/i })).toBeInTheDocument()
      })
      expect(screen.queryByText('✓')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /copied/i })).not.toBeInTheDocument()
    })
  })

  describe('Report Issue button', () => {
    it('calls onReportIssue when clicked', () => {
      render(<ResponseFooter {...defaultProps} />)

      const button = screen.getByRole('button', { name: /report an issue/i })
      expect(button).toBeEnabled()
      fireEvent.click(button)

      expect(defaultProps.onReportIssue).toHaveBeenCalledTimes(1)
    })
  })

  it('hides sources button when sourceCount is 0', () => {
    render(<ResponseFooter sourceCount={0} />)

    // Sources button should not render when there are no sources
    expect(screen.queryByRole('button', { name: /view.*sources/i })).not.toBeInTheDocument()

    // Other buttons should still render and not throw when clicked without callbacks
    fireEvent.click(screen.getByRole('button', { name: /copy response/i }))
    fireEvent.click(screen.getByRole('button', { name: /report an issue/i }))
  })
})
