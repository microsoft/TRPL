/**
 * @jest-environment jsdom
 */

import { act, render, screen, waitFor } from '@testing-library/react'
import { Gallery } from '../Gallery'
import type { GalleryImage } from '../VerticalGallery'

const mediaQueryListeners = new Set<(event: MediaQueryListEvent) => void>()
let desktopMatches = true
const mockMediaQueryList = {
  get matches() {
    return desktopMatches
  },
  media: '(min-width: 1200px)',
  onchange: null,
  addEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => {
    mediaQueryListeners.add(listener)
  },
  removeEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => {
    mediaQueryListeners.delete(listener)
  },
  addListener: (listener: (event: MediaQueryListEvent) => void) => {
    mediaQueryListeners.add(listener)
  },
  removeListener: (listener: (event: MediaQueryListEvent) => void) => {
    mediaQueryListeners.delete(listener)
  },
  dispatchEvent: () => true,
} as unknown as MediaQueryList

const setInitialDesktopMatch = (matches: boolean) => {
  desktopMatches = matches
}

const setDesktopMatch = (matches: boolean) => {
  act(() => {
    desktopMatches = matches
    const event = { matches, media: mockMediaQueryList.media } as MediaQueryListEvent
    mediaQueryListeners.forEach((listener) => listener(event))
  })
}

// Mock child components
jest.mock('../VerticalGallery', () => ({
  VerticalGallery: ({ images, scrollSpeed, className, columns, autoScroll, showActions, onImageAction }: {
    images: GalleryImage[]
    scrollSpeed?: number
    className?: string
    columns?: number
    autoScroll?: boolean
    showActions?: boolean
    onImageAction?: (image: GalleryImage) => void
  }) => (
    <div
      data-testid="vertical-gallery"
      data-image-count={images.length}
      data-scroll-speed={scrollSpeed}
      data-columns={columns}
      data-auto-scroll={autoScroll}
      data-show-actions={showActions}
      data-has-action-handler={onImageAction ? 'true' : 'false'}
      className={className}
    >
      VerticalGallery
    </div>
  ),
}))

jest.mock('../HorizontalGallery', () => ({
  HorizontalGallery: ({ images, scrollSpeed, className, onImageAction }: {
    images: GalleryImage[]
    scrollSpeed?: number
    className?: string
    onImageAction?: (image: GalleryImage) => void
  }) => (
    <div
      data-testid="horizontal-gallery"
      data-image-count={images.length}
      data-scroll-speed={scrollSpeed}
      data-has-action-handler={onImageAction ? 'true' : 'false'}
      className={className}
    >
      HorizontalGallery
    </div>
  ),
}))

describe('Gallery', () => {
  const mockImages: GalleryImage[] = [
    { id: '1', src: '/img1.jpg', alt: 'Image 1' },
    { id: '2', src: '/img2.jpg', alt: 'Image 2' },
    { id: '3', src: '/img3.jpg', alt: 'Image 3' },
  ]

  beforeEach(() => {
    mediaQueryListeners.clear()
    setInitialDesktopMatch(true)
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: jest.fn().mockImplementation(() => mockMediaQueryList),
    })
  })

  it('renders vertical gallery on desktop', async () => {
    render(<Gallery images={mockImages} />)

    await waitFor(() => {
      expect(screen.getByTestId('vertical-gallery')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('horizontal-gallery')).not.toBeInTheDocument()
  })

  it('renders horizontal gallery on mobile', async () => {
    setInitialDesktopMatch(false)
    render(<Gallery images={mockImages} />)

    await waitFor(() => {
      expect(screen.getByTestId('horizontal-gallery')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('vertical-gallery')).not.toBeInTheDocument()
  })

  it('switches variants on media query change', async () => {
    render(<Gallery images={mockImages} />)

    await waitFor(() => {
      expect(screen.getByTestId('vertical-gallery')).toBeInTheDocument()
    })

    setDesktopMatch(false)
    await waitFor(() => {
      expect(screen.getByTestId('horizontal-gallery')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('vertical-gallery')).not.toBeInTheDocument()
  })

  it('passes desktop-only props to vertical gallery', async () => {
    render(
      <Gallery
        images={mockImages}
        scrollSpeed={50}
        className="custom-gallery"
        columns={1}
        autoScroll={false}
        showActions={true}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('vertical-gallery')).toBeInTheDocument()
    })

    const verticalGallery = screen.getByTestId('vertical-gallery')
    expect(verticalGallery).toHaveAttribute('data-scroll-speed', '50')
    expect(verticalGallery).toHaveAttribute('data-columns', '1')
    expect(verticalGallery).toHaveAttribute('data-auto-scroll', 'false')
    expect(verticalGallery).toHaveAttribute('data-show-actions', 'true')
    expect(verticalGallery).toHaveClass('custom-gallery')
  })

  it('passes mobile props to horizontal gallery', async () => {
    setInitialDesktopMatch(false)
    render(
      <Gallery
        images={mockImages}
        scrollSpeed={40}
        className="custom-gallery"
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('horizontal-gallery')).toBeInTheDocument()
    })

    const horizontalGallery = screen.getByTestId('horizontal-gallery')
    expect(horizontalGallery).toHaveAttribute('data-scroll-speed', '40')
    expect(horizontalGallery).toHaveClass('custom-gallery')
  })

  it('passes onImageAction to active variant', async () => {
    const mockOnImageAction = jest.fn()
    render(<Gallery images={mockImages} onImageAction={mockOnImageAction} />)

    await waitFor(() => {
      expect(screen.getByTestId('vertical-gallery')).toBeInTheDocument()
    })
    expect(screen.getByTestId('vertical-gallery')).toHaveAttribute('data-has-action-handler', 'true')

    setDesktopMatch(false)
    await waitFor(() => {
      expect(screen.getByTestId('horizontal-gallery')).toBeInTheDocument()
    })
    expect(screen.getByTestId('horizontal-gallery')).toHaveAttribute('data-has-action-handler', 'true')
  })

  it('renders with empty images array', async () => {
    render(<Gallery images={[]} />)

    await waitFor(() => {
      expect(screen.getByTestId('vertical-gallery')).toBeInTheDocument()
    })
    expect(screen.getByTestId('vertical-gallery')).toHaveAttribute('data-image-count', '0')
  })
})
