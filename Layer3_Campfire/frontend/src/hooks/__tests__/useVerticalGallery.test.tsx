import { renderHook, act } from '@testing-library/react'
import { useVerticalGallery } from '../useVerticalGallery'

// Mock usePrefersReducedMotion
jest.mock('../usePrefersReducedMotion', () => ({
  usePrefersReducedMotion: jest.fn(() => false),
}))

// Mock Web Animations API
const mockAnimate = jest.fn(() => ({
  play: jest.fn(),
  pause: jest.fn(),
  cancel: jest.fn(),
  currentTime: 0,
}))

// Mock ResizeObserver
class MockResizeObserver {
  observe = jest.fn()
  unobserve = jest.fn()
  disconnect = jest.fn()
}

global.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver

describe('useVerticalGallery', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    Element.prototype.animate = mockAnimate as any
  })

  describe('initialization', () => {
    it('returns expected shape', () => {
      const { result } = renderHook(() => useVerticalGallery())

      expect(result.current).toEqual({
        containerRef: expect.any(Object),
        trackRef: expect.any(Object),
        selectedImageId: null,
        handleImageHover: expect.any(Function),
        handleImageLeave: expect.any(Function),
        pauseForKeyboard: expect.any(Function),
        resumeFromKeyboard: expect.any(Function),
        isKeyboardMode: false,
      })
    })

    it('initializes with no selected image', () => {
      const { result } = renderHook(() => useVerticalGallery())
      expect(result.current.selectedImageId).toBeNull()
    })

    it('initializes with keyboard mode off', () => {
      const { result } = renderHook(() => useVerticalGallery())
      expect(result.current.isKeyboardMode).toBe(false)
    })
  })

  describe('handleImageHover', () => {
    it('sets selected image id', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.handleImageHover('image-1')
      })

      expect(result.current.selectedImageId).toBe('image-1')
    })

    it('updates selected image when hovering different image', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.handleImageHover('image-1')
      })
      expect(result.current.selectedImageId).toBe('image-1')

      act(() => {
        result.current.handleImageHover('image-2')
      })
      expect(result.current.selectedImageId).toBe('image-2')
    })

    it('does not update if hovering same image', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.handleImageHover('image-1')
      })

      const firstSelectedId = result.current.selectedImageId

      act(() => {
        result.current.handleImageHover('image-1')
      })

      expect(result.current.selectedImageId).toBe(firstSelectedId)
    })
  })

  describe('handleImageLeave', () => {
    // handleImageLeave is debounced (250ms) to allow moving between cards
    const HOVER_DEBOUNCE_MS = 250

    beforeEach(() => {
      jest.useFakeTimers()
    })

    afterEach(() => {
      jest.useRealTimers()
    })

    it('clears selected image when leaving (after debounce)', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.handleImageHover('image-1')
      })
      expect(result.current.selectedImageId).toBe('image-1')

      act(() => {
        result.current.handleImageLeave()
      })
      // Selection not cleared yet due to debounce
      expect(result.current.selectedImageId).toBe('image-1')

      // Advance past debounce
      act(() => {
        jest.advanceTimersByTime(HOVER_DEBOUNCE_MS)
      })
      expect(result.current.selectedImageId).toBeNull()
    })

    it('clears selection when leaving specific image that is selected (after debounce)', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.handleImageHover('image-1')
      })

      act(() => {
        result.current.handleImageLeave('image-1')
      })

      // Advance past debounce
      act(() => {
        jest.advanceTimersByTime(HOVER_DEBOUNCE_MS)
      })

      expect(result.current.selectedImageId).toBeNull()
    })

    it('does not clear selection when leaving different image', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.handleImageHover('image-1')
      })

      act(() => {
        result.current.handleImageLeave('image-2')
      })

      // Advance past debounce - should still not clear since different image
      act(() => {
        jest.advanceTimersByTime(HOVER_DEBOUNCE_MS)
      })

      expect(result.current.selectedImageId).toBe('image-1')
    })

    it('cancels leave when hovering a new image before debounce completes', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.handleImageHover('image-1')
      })

      act(() => {
        result.current.handleImageLeave()
      })

      // Hover a new image before debounce completes
      act(() => {
        jest.advanceTimersByTime(100) // Less than debounce
        result.current.handleImageHover('image-2')
      })

      // Advance past original debounce time
      act(() => {
        jest.advanceTimersByTime(HOVER_DEBOUNCE_MS)
      })

      // Should now show image-2, not null
      expect(result.current.selectedImageId).toBe('image-2')
    })
  })

  describe('keyboard navigation mode', () => {
    it('pauseForKeyboard enables keyboard mode', () => {
      const { result } = renderHook(() => useVerticalGallery())

      expect(result.current.isKeyboardMode).toBe(false)

      act(() => {
        result.current.pauseForKeyboard()
      })

      expect(result.current.isKeyboardMode).toBe(true)
    })

    it('resumeFromKeyboard disables keyboard mode', () => {
      const { result } = renderHook(() => useVerticalGallery())

      act(() => {
        result.current.pauseForKeyboard()
      })
      expect(result.current.isKeyboardMode).toBe(true)

      act(() => {
        result.current.resumeFromKeyboard()
      })
      expect(result.current.isKeyboardMode).toBe(false)
    })
  })

  describe('options', () => {
    it('accepts scrollSpeed option', () => {
      const { result } = renderHook(() =>
        useVerticalGallery({ scrollSpeed: 50 })
      )
      expect(result.current).toBeDefined()
    })

    it('accepts enabled option', () => {
      const { result } = renderHook(() =>
        useVerticalGallery({ enabled: false })
      )
      expect(result.current).toBeDefined()
    })

    it('accepts duplicateSets option', () => {
      const { result } = renderHook(() =>
        useVerticalGallery({ duplicateSets: 3 })
      )
      expect(result.current).toBeDefined()
    })

    it('uses default values when no options provided', () => {
      const { result } = renderHook(() => useVerticalGallery())
      expect(result.current).toBeDefined()
    })
  })

  describe('refs', () => {
    it('provides containerRef', () => {
      const { result } = renderHook(() => useVerticalGallery())
      expect(result.current.containerRef).toHaveProperty('current')
    })

    it('provides trackRef', () => {
      const { result } = renderHook(() => useVerticalGallery())
      expect(result.current.trackRef).toHaveProperty('current')
    })
  })

  describe('reduced motion', () => {
    it('respects prefers-reduced-motion', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { usePrefersReducedMotion } = require('../usePrefersReducedMotion')
      usePrefersReducedMotion.mockReturnValue(true)

      const { result } = renderHook(() => useVerticalGallery())

      // Hook should still return valid shape even with reduced motion
      expect(result.current.containerRef).toBeDefined()
      expect(result.current.trackRef).toBeDefined()
      expect(typeof result.current.handleImageHover).toBe('function')
    })
  })

  describe('cleanup', () => {
    it('cleans up on unmount', () => {
      const { unmount } = renderHook(() => useVerticalGallery())

      // Should not throw on unmount
      expect(() => unmount()).not.toThrow()
    })

    it('cleans up when enabled changes to false', () => {
      const { rerender } = renderHook(
        ({ enabled }) => useVerticalGallery({ enabled }),
        { initialProps: { enabled: true } }
      )

      // Should not throw when disabled
      expect(() => rerender({ enabled: false })).not.toThrow()
    })
  })
})
