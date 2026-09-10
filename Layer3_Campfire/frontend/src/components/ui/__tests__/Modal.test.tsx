import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { Modal } from '../Modal/Modal'

describe('Modal', () => {
  it('renders children when open', () => {
    render(
      <Modal open={true} onOpenChange={jest.fn()}>
        <span>Modal content</span>
      </Modal>
    )
    expect(screen.getByText('Modal content')).toBeInTheDocument()
  })

  it('does not render when closed', () => {
    render(
      <Modal open={false} onOpenChange={jest.fn()}>
        <span>Modal content</span>
      </Modal>
    )
    expect(screen.queryByText('Modal content')).not.toBeInTheDocument()
  })

  it('renders title when provided', () => {
    render(
      <Modal open={true} onOpenChange={jest.fn()} title="Test Title">
        <span>Content</span>
      </Modal>
    )
    expect(screen.getByText('Test Title')).toBeInTheDocument()
  })

  it('does not render title element when not provided', () => {
    render(
      <Modal open={true} onOpenChange={jest.fn()}>
        <span>Content</span>
      </Modal>
    )
    // Look for heading role which would exist if title was rendered
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  })

  it('renders close button', () => {
    render(
      <Modal open={true} onOpenChange={jest.fn()}>
        <span>Content</span>
      </Modal>
    )
    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument()
  })

  it('calls onOpenChange with false when close button clicked', () => {
    const handleOpenChange = jest.fn()
    render(
      <Modal open={true} onOpenChange={handleOpenChange}>
        <span>Content</span>
      </Modal>
    )

    fireEvent.click(screen.getByRole('button', { name: /close/i }))

    // Base UI Dialog passes (open, event, reason) to onOpenChange
    expect(handleOpenChange).toHaveBeenCalled()
    expect(handleOpenChange.mock.calls[0][0]).toBe(false)
  })

  it('calls onOpenChange when backdrop is clicked', async () => {
    const handleOpenChange = jest.fn()
    render(
      <Modal open={true} onOpenChange={handleOpenChange}>
        <span>Content</span>
      </Modal>
    )

    // Find the backdrop by class
    const backdrop = document.querySelector('[class*="backdrop"]')
    expect(backdrop).toBeInTheDocument()

    if (backdrop) {
      fireEvent.click(backdrop)
    }

    await waitFor(() => {
      // Base UI Dialog passes (open, event, reason) to onOpenChange
      expect(handleOpenChange).toHaveBeenCalled()
      expect(handleOpenChange.mock.calls[0][0]).toBe(false)
    })
  })

  it('applies custom className to popup', () => {
    render(
      <Modal open={true} onOpenChange={jest.fn()} className="custom-modal">
        <span>Content</span>
      </Modal>
    )
    const popup = document.querySelector('[class*="popup"]')
    expect(popup).toHaveClass('custom-modal')
  })

  it('renders backdrop with correct class', () => {
    render(
      <Modal open={true} onOpenChange={jest.fn()}>
        <span>Content</span>
      </Modal>
    )
    const backdrop = document.querySelector('[class*="backdrop"]')
    expect(backdrop).toBeInTheDocument()
  })

  it('can transition from closed to open', () => {
    const { rerender } = render(
      <Modal open={false} onOpenChange={jest.fn()}>
        <span>Content</span>
      </Modal>
    )
    expect(screen.queryByText('Content')).not.toBeInTheDocument()

    rerender(
      <Modal open={true} onOpenChange={jest.fn()}>
        <span>Content</span>
      </Modal>
    )
    expect(screen.getByText('Content')).toBeInTheDocument()
  })

  it('can transition from open to closed', () => {
    const { rerender } = render(
      <Modal open={true} onOpenChange={jest.fn()}>
        <span>Content</span>
      </Modal>
    )
    expect(screen.getByText('Content')).toBeInTheDocument()

    rerender(
      <Modal open={false} onOpenChange={jest.fn()}>
        <span>Content</span>
      </Modal>
    )
    expect(screen.queryByText('Content')).not.toBeInTheDocument()
  })

  it('renders complex children', () => {
    render(
      <Modal open={true} onOpenChange={jest.fn()} title="Form">
        <form>
          <label htmlFor="name">Name</label>
          <input id="name" type="text" />
          <button type="submit">Submit</button>
        </form>
      </Modal>
    )
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument()
  })
})
