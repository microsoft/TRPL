/**
 * @jest-environment jsdom
 */

import { render, screen } from '@testing-library/react'
import { VerticalGallery } from '../VerticalGallery'
import type { GalleryImage } from '../VerticalGallery'

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string; width: number; height: number }) => (
    <img src={props.src} alt={props.alt} width={props.width} height={props.height} />
  ),
}))

// Mock useVerticalGallery hook
const mockHandleImageHover = jest.fn()
const mockHandleImageLeave = jest.fn()
const mockPauseForKeyboard = jest.fn()
const mockResumeFromKeyboard = jest.fn()

jest.mock('@/hooks/useVerticalGallery', () => ({
  useVerticalGallery: () => ({
    containerRef: { current: null },
    trackRef: { current: null },
    selectedImageId: null,
    handleImageHover: mockHandleImageHover,
    handleImageLeave: mockHandleImageLeave,
    pauseForKeyboard: mockPauseForKeyboard,
    resumeFromKeyboard: mockResumeFromKeyboard,
    isKeyboardMode: false,
  }),
}))

// Mock ResizeObserver
class MockResizeObserver {
  observe = jest.fn()
  unobserve = jest.fn()
  disconnect = jest.fn()
}

global.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver

// Mock constants
jest.mock('@/lib/constants', () => ({
  GALLERY: {
    ESTIMATED_IMAGE_HEIGHT: 120,
    SSR_VIEWPORT_HEIGHT: 1080,
    DEFAULT_DUPLICATE_SETS: 2,
  },
}))

describe('VerticalGallery', () => {
  const mockImages: GalleryImage[] = [
    { id: '1', src: '/img1.jpg', alt: 'Image 1', caption: 'Caption 1' },
    { id: '2', src: '/img2.jpg', alt: 'Image 2' },
    { id: '3', src: '/img3.jpg', alt: 'Image 3', caption: 'Caption 3' },
    { id: '4', src: '/img4.jpg', alt: 'Image 4' },
  ]

  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe('rendering', () => {
    it('renders gallery container', () => {
      render(<VerticalGallery images={mockImages} />)

      expect(screen.getByRole('region', { name: /Image gallery/ })).toBeInTheDocument()
    })

    it('renders images', () => {
      render(<VerticalGallery images={mockImages} />)

      // Images are duplicated for infinite scroll, so check for multiple instances
      const images = screen.getAllByRole('img')
      expect(images.length).toBeGreaterThan(0)
    })

    it('renders captions when provided', () => {
      render(<VerticalGallery images={mockImages} />)

      // Multiple instances due to duplication
      const caption1Elements = screen.getAllByText('Caption 1')
      expect(caption1Elements.length).toBeGreaterThan(0)
    })

    it('uses alt text as caption fallback when caption not provided', () => {
      render(<VerticalGallery images={mockImages} />)

      // Image 2 has no caption, so should display its alt text
      const altCaptions = screen.getAllByText('Image 2')
      expect(altCaptions.length).toBeGreaterThan(0)
    })

    it('is focusable', () => {
      render(<VerticalGallery images={mockImages} />)

      expect(screen.getByRole('region')).toHaveAttribute('tabIndex', '0')
    })
  })

  describe('empty state', () => {
    it('renders empty message when no images', () => {
      render(<VerticalGallery images={[]} />)

      expect(screen.getByText('No images to display')).toBeInTheDocument()
    })
  })

  describe('column layout', () => {
    it('renders two columns by default', () => {
      const { container } = render(<VerticalGallery images={mockImages} />)

      const columns = container.querySelectorAll('[data-column]')
      expect(columns.length).toBe(2)
    })

    it('renders single column when columns=1', () => {
      const { container } = render(<VerticalGallery images={mockImages} columns={1} />)

      const columns = container.querySelectorAll('[data-column]')
      expect(columns.length).toBe(1)
    })
  })

  describe('accessibility', () => {
    it('has descriptive aria-label', () => {
      render(<VerticalGallery images={mockImages} />)

      expect(screen.getByRole('region', { name: 'Image gallery. Use Tab to browse images.' })).toBeInTheDocument()
    })

    it('image wrappers have role="button"', () => {
      render(<VerticalGallery images={mockImages} />)

      const buttons = screen.getAllByRole('button', { name: /View/ })
      expect(buttons.length).toBeGreaterThan(0)
    })
  })

  describe('props', () => {
    it('accepts custom className', () => {
      render(<VerticalGallery images={mockImages} className="custom-gallery" />)

      expect(screen.getByRole('region')).toHaveClass('custom-gallery')
    })

    it('accepts scrollSpeed prop', () => {
      // Just verify it doesn't throw
      expect(() => render(<VerticalGallery images={mockImages} scrollSpeed={50} />)).not.toThrow()
    })

    it('accepts autoScroll prop', () => {
      expect(() => render(<VerticalGallery images={mockImages} autoScroll={false} />)).not.toThrow()
    })

    it('accepts showActions prop', () => {
      render(<VerticalGallery images={mockImages} showActions={true} />)

      const actionButtons = screen.getAllByText('View Details', { selector: 'button' })
      expect(actionButtons.length).toBeGreaterThan(0)
    })
  })
})
