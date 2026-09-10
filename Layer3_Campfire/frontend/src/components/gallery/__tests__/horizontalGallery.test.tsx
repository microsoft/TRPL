/**
 * @jest-environment jsdom
 */

import { render, screen } from '@testing-library/react'
import { HorizontalGallery, getFullyVisibleImages } from '../HorizontalGallery'
import type { GalleryImage } from '../HorizontalGallery'

// Mock next/image
jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string; width: number; height: number }) => (
    <img src={props.src} alt={props.alt} width={props.width} height={props.height} />
  ),
}))

// Mock useHorizontalGallery hook
const mockHandleContainerClick = jest.fn()
const mockPauseForKeyboard = jest.fn()
const mockResumeFromKeyboard = jest.fn()
const mockApplySelectionState = jest.fn()
const mockClearSelection = jest.fn()

jest.mock('@/hooks/useHorizontalGallery', () => ({
  useHorizontalGallery: () => ({
    containerRef: { current: null },
    trackRef: { current: null },
    handleContainerClick: mockHandleContainerClick,
    pauseForKeyboard: mockPauseForKeyboard,
    resumeFromKeyboard: mockResumeFromKeyboard,
    isKeyboardMode: false,
    applySelectionState: mockApplySelectionState,
    clearSelection: mockClearSelection,
  }),
}))

// ============================================================================
// getFullyVisibleImages Unit Tests
// ============================================================================

const makeRect = (left: number, right: number, top = 0, bottom = 100) => ({
  left,
  right,
  top,
  bottom,
  width: right - left,
  height: bottom - top,
})

describe('getFullyVisibleImages', () => {
  let container: HTMLElement

  beforeEach(() => {
    container = document.createElement('div')
    // Mock container rect
    jest.spyOn(container, 'getBoundingClientRect').mockReturnValue(makeRect(0, 300, 0, 200) as DOMRect)
  })

  it('returns only fully visible items within container bounds', () => {
    const fullyVisible = document.createElement('div')
    const partiallyVisible = document.createElement('div')
    const offscreen = document.createElement('div')

    fullyVisible.setAttribute('data-gallery-image', 'true')
    partiallyVisible.setAttribute('data-gallery-image', 'true')
    offscreen.setAttribute('data-gallery-image', 'true')

    jest.spyOn(fullyVisible, 'getBoundingClientRect').mockReturnValue(makeRect(20, 120) as DOMRect)
    jest.spyOn(partiallyVisible, 'getBoundingClientRect').mockReturnValue(makeRect(250, 350) as DOMRect)
    jest.spyOn(offscreen, 'getBoundingClientRect').mockReturnValue(makeRect(310, 410) as DOMRect)

    container.append(fullyVisible, partiallyVisible, offscreen)

    const result = getFullyVisibleImages(container)
    expect(result).toHaveLength(1)
    expect(result[0]).toBe(fullyVisible)
  })

  it('sorts by row (top) then x position within a row', () => {
    const topLeft = document.createElement('div')
    const topRight = document.createElement('div')
    const bottomLeft = document.createElement('div')

    topLeft.setAttribute('data-gallery-image', 'true')
    topRight.setAttribute('data-gallery-image', 'true')
    bottomLeft.setAttribute('data-gallery-image', 'true')

    jest.spyOn(topLeft, 'getBoundingClientRect').mockReturnValue(makeRect(20, 70, 0, 50) as DOMRect)
    jest.spyOn(topRight, 'getBoundingClientRect').mockReturnValue(makeRect(80, 130, 0, 50) as DOMRect)
    jest.spyOn(bottomLeft, 'getBoundingClientRect').mockReturnValue(makeRect(10, 60, 80, 130) as DOMRect)

    container.append(bottomLeft, topRight, topLeft)

    const result = getFullyVisibleImages(container)
    expect(result).toEqual([topLeft, topRight, bottomLeft])
  })
})

// ============================================================================
// HorizontalGallery Component Tests
// ============================================================================

describe('HorizontalGallery', () => {
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
      render(<HorizontalGallery images={mockImages} />)

      expect(screen.getByRole('region', { name: /Image gallery/ })).toBeInTheDocument()
    })

    it('renders images in two rows', () => {
      const { container } = render(<HorizontalGallery images={mockImages} />)

      const rows = container.querySelectorAll('[data-row]')
      expect(rows.length).toBe(2)
    })

    it('renders top row', () => {
      const { container } = render(<HorizontalGallery images={mockImages} />)

      expect(container.querySelector('[data-row="top"]')).toBeInTheDocument()
    })

    it('renders bottom row', () => {
      const { container } = render(<HorizontalGallery images={mockImages} />)

      expect(container.querySelector('[data-row="bottom"]')).toBeInTheDocument()
    })

    it('renders captions when provided', () => {
      render(<HorizontalGallery images={mockImages} />)

      // Multiple instances due to triplication for infinite scroll
      const captions = screen.getAllByText('Caption 1')
      expect(captions.length).toBeGreaterThan(0)
    })

    it('uses alt text as caption fallback when caption not provided', () => {
      render(<HorizontalGallery images={mockImages} />)

      // Image 2 has no caption, so should display its alt text
      const altCaptions = screen.getAllByText('Image 2')
      expect(altCaptions.length).toBeGreaterThan(0)
    })

    it('is focusable', () => {
      render(<HorizontalGallery images={mockImages} />)

      // The focus wrapper should be focusable
      const focusWrapper = screen.getByRole('region')
      expect(focusWrapper).toHaveAttribute('tabIndex', '0')
    })
  })

  describe('empty state', () => {
    it('renders empty message when no images', () => {
      render(<HorizontalGallery images={[]} />)

      expect(screen.getByText('No images to display')).toBeInTheDocument()
    })
  })

  describe('accessibility', () => {
    it('has descriptive aria-label', () => {
      render(<HorizontalGallery images={mockImages} />)

      expect(screen.getByRole('region', { name: 'Image gallery. Use Tab to browse images.' })).toBeInTheDocument()
    })

    it('image wrappers expose link role with descriptive labels', () => {
      render(<HorizontalGallery images={mockImages} />)

      const links = screen.getAllByRole('link', { name: /View/ })
      expect(links.length).toBeGreaterThan(0)
    })
  })

  describe('props', () => {
    it('accepts custom className', () => {
      render(<HorizontalGallery images={mockImages} className="custom-gallery" />)

      expect(screen.getByRole('region')).toHaveClass('custom-gallery')
    })

    it('accepts scrollSpeed prop', () => {
      expect(() => render(<HorizontalGallery images={mockImages} scrollSpeed={50} />)).not.toThrow()
    })
  })

  describe('image triplication', () => {
    it('triplicates images for seamless scrolling', () => {
      render(<HorizontalGallery images={mockImages} />)

      // With 4 images split into 2 rows (2 per row), each row is tripled
      // So we should see at least 6 instances of images from each row
      const allImages = screen.getAllByRole('img')
      expect(allImages.length).toBeGreaterThanOrEqual(mockImages.length * 3)
    })
  })
})
