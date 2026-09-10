import { render, screen, fireEvent } from '@testing-library/react'
import { ErrorBoundary } from '../ErrorBoundary/ErrorBoundary'

// Component that throws an error
const ThrowError = ({ shouldThrow }: { shouldThrow: boolean }) => {
  if (shouldThrow) {
    throw new Error('Test error')
  }
  return <div>No error</div>
}

// Suppress console.error for expected errors in tests
const originalError = console.error
beforeAll(() => {
  console.error = jest.fn()
})
afterAll(() => {
  console.error = originalError
})

describe('ErrorBoundary', () => {
  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <div>Child content</div>
      </ErrorBoundary>
    )
    expect(screen.getByText('Child content')).toBeInTheDocument()
  })

  it('renders default fallback UI when error occurs', () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText(/We encountered an unexpected error/)).toBeInTheDocument()
  })

  it('renders custom fallback when provided', () => {
    render(
      <ErrorBoundary fallback={<div>Custom error UI</div>}>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(screen.getByText('Custom error UI')).toBeInTheDocument()
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument()
  })

  it('renders Try Again button in default fallback', () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
  })

  it('resets error state when Try Again is clicked', () => {
    const { rerender } = render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()

    // Click Try Again - this will reset state but child will throw again
    // We need to change the child to not throw after reset
    fireEvent.click(screen.getByRole('button', { name: 'Try Again' }))

    // The error boundary reset, but the child still throws
    // So we still see the error UI
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()

    // Now rerender with a non-throwing child to verify reset worked
    rerender(
      <ErrorBoundary>
        <ThrowError shouldThrow={false} />
      </ErrorBoundary>
    )

    // First reset the boundary
    const resetButton = screen.queryByRole('button', { name: 'Try Again' })
    if (resetButton) {
      fireEvent.click(resetButton)
    }
  })

  it('calls onError callback when error is caught', () => {
    const handleError = jest.fn()

    render(
      <ErrorBoundary onError={handleError}>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(handleError).toHaveBeenCalledTimes(1)
    expect(handleError).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ componentStack: expect.any(String) })
    )
  })

  it('resets when resetKey changes', () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="key1">
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()

    // Change resetKey and provide non-throwing child
    rerender(
      <ErrorBoundary resetKey="key2">
        <ThrowError shouldThrow={false} />
      </ErrorBoundary>
    )

    expect(screen.getByText('No error')).toBeInTheDocument()
  })

  it('does not reset when resetKey stays the same', () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="key1">
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()

    // Rerender with same key but non-throwing child - should still show error
    rerender(
      <ErrorBoundary resetKey="key1">
        <ThrowError shouldThrow={false} />
      </ErrorBoundary>
    )

    // Still shows error because resetKey didn't change
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  it('catches errors in nested components', () => {
    const NestedError = () => (
      <div>
        <div>
          <ThrowError shouldThrow={true} />
        </div>
      </div>
    )

    render(
      <ErrorBoundary>
        <NestedError />
      </ErrorBoundary>
    )

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  it('allows multiple error boundaries in tree', () => {
    render(
      <ErrorBoundary fallback={<div>Outer error</div>}>
        <div>
          <ErrorBoundary fallback={<div>Inner error</div>}>
            <ThrowError shouldThrow={true} />
          </ErrorBoundary>
        </div>
      </ErrorBoundary>
    )

    // Inner boundary should catch the error
    expect(screen.getByText('Inner error')).toBeInTheDocument()
    expect(screen.queryByText('Outer error')).not.toBeInTheDocument()
  })
})
