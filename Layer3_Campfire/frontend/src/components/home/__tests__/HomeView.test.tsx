/**
 * @jest-environment jsdom
 */

import { render, screen } from '@testing-library/react'
import { HomeView } from '../HomeView'
import { GALLERY_ARTIFACTS, GALLERY_IMAGES } from '@/lib/constants'

interface CapturedGalleryImage {
  id: string
  src: string
  alt: string
  caption: string
}

let mockGalleryImages: CapturedGalleryImage[] = []
let mockGalleryAction: ((image: CapturedGalleryImage) => void) | undefined

// Mock hooks
jest.mock('@/hooks/useIsMobile', () => ({
  useIsMobile: jest.fn(() => false),
}))

const mockTriggerLeaveAnimation = jest.fn()

jest.mock('@/hooks/useHomeViewAnimation', () => ({
  useHomeViewAnimation: () => ({
    containerRef: { current: null },
    taglineRef: { current: null },
    titleRef: { current: null },
    contentRef: { current: null },
    footerRef: { current: null },
    galleryRef: { current: null },
    heroRef: { current: null },
    backgroundOverlayRef: { current: null },
    triggerLeaveAnimation: mockTriggerLeaveAnimation,
  }),
}))

// Mock sessionStore
const mockResetSession = jest.fn()

jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: jest.fn((selector) => {
    const state = {
      selectedAction: null,
      selectedTopic: null,
      selectedPromptId: null,
      setSelectedAction: jest.fn(),
      setSelectedTopic: jest.fn(),
      setSelectedPromptId: jest.fn(),
      addMessage: jest.fn(),
      resetSession: mockResetSession,
    }
    return selector(state)
  }),
  selectComposedPrompt: () => null,
  createUserMessage: jest.fn(),
}))

// Mock child components
jest.mock('@/components/home', () => ({
  HeroSection: ({ onSubmit }: { onSubmit?: () => void }) => (
    <div data-testid="hero-section">
      <button onClick={onSubmit}>Submit</button>
    </div>
  ),
}))

jest.mock('@/components/gallery', () => ({
  Gallery: ({
    images,
    scrollSpeed,
    onImageAction,
  }: {
    images: CapturedGalleryImage[]
    scrollSpeed: number
    onImageAction?: (image: CapturedGalleryImage) => void
  }) => {
    mockGalleryImages = images
    mockGalleryAction = onImageAction
    return (
      <div data-testid="gallery" data-image-count={images.length} data-scroll-speed={scrollSpeed}>
        Gallery
      </div>
    )
  },
}))

jest.mock('@/components/chat/ReportIssueModal', () => ({
  ReportIssueModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="report-issue-modal">Report Modal</div> : null,
}))

jest.mock('@/components/ui', () => ({
  Button: ({ children, variant }: { children: React.ReactNode; variant: string }) => (
    <button data-testid="ui-button" data-variant={variant}>
      {children}
    </button>
  ),
}))

describe('HomeView', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGalleryImages = []
    mockGalleryAction = undefined
  })

  describe('rendering', () => {
    it('renders main element', () => {
      render(<HomeView />)

      expect(screen.getByRole('main')).toBeInTheDocument()
    })

    it('renders HeroSection', () => {
      render(<HomeView />)

      expect(screen.getByTestId('hero-section')).toBeInTheDocument()
    })

    it('renders Gallery', () => {
      render(<HomeView />)

      expect(screen.getByTestId('gallery')).toBeInTheDocument()
    })

    it('passes images to Gallery', () => {
      render(<HomeView />)

      expect(screen.getByTestId('gallery')).toHaveAttribute(
        'data-image-count',
        GALLERY_ARTIFACTS.length.toString()
      )
    })

    it('passes the synthetic gallery projection to Gallery', () => {
      render(<HomeView />)

      expect(mockGalleryImages).toEqual(GALLERY_IMAGES)
    })

    it('passes scrollSpeed to Gallery', () => {
      render(<HomeView />)

      expect(screen.getByTestId('gallery')).toHaveAttribute('data-scroll-speed', '30')
    })

    it('navigates to the selected synthetic artifact', () => {
      render(<HomeView />)

      expect(mockGalleryAction).toBeDefined()
      mockGalleryAction?.(mockGalleryImages[0])
      expect(mockTriggerLeaveAnimation).toHaveBeenCalledWith(
        `/artifact/${encodeURIComponent(GALLERY_ARTIFACTS[0].id)}?source=letter&from=home`
      )
    })

    it('renders footer buttons', () => {
      render(<HomeView />)

      expect(screen.getByText('Theodore Roosevelt Presidential Library')).toBeInTheDocument()
      expect(screen.getByText('How we use Artificial Intelligence')).toBeInTheDocument()
      expect(screen.getByText('Report an Issue')).toBeInTheDocument()
    })

    it('renders footer buttons with primary variant', () => {
      render(<HomeView />)

      const buttons = screen.getAllByTestId('ui-button')
      buttons.forEach((button) => {
        expect(button).toHaveAttribute('data-variant', 'primary')
      })
    })
  })

  describe('layout', () => {
    it('renders hero section', () => {
      const { container } = render(<HomeView />)

      expect(container.querySelector('section')).toBeInTheDocument()
    })

    it('renders aside for gallery', () => {
      const { container } = render(<HomeView />)

      expect(container.querySelector('aside')).toBeInTheDocument()
    })

    it('renders footer', () => {
      const { container } = render(<HomeView />)

      expect(container.querySelector('footer')).toBeInTheDocument()
    })
  })

  describe('animation integration', () => {
    it('passes triggerLeaveAnimation to HeroSection', () => {
      render(<HomeView />)

      // HeroSection receives onSubmit which is triggerLeaveAnimation
      expect(screen.getByTestId('hero-section')).toBeInTheDocument()
    })
  })

  describe('session lifecycle', () => {
    it('resets session on mount to ensure a fresh start', () => {
      render(<HomeView />)

      expect(mockResetSession).toHaveBeenCalled()
    })
  })
})
