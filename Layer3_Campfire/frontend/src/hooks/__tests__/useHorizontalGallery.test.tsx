/**
 * @jest-environment jsdom
 */

import { renderHook, act } from '@testing-library/react'
import { useHorizontalGallery } from '../useHorizontalGallery'

// Mock usePrefersReducedMotion
let mockPrefersReducedMotion = false
jest.mock('../usePrefersReducedMotion', () => ({
  usePrefersReducedMotion: () => mockPrefersReducedMotion,
}))

// Mock Web Animations API
const mockAnimate = jest.fn(() => ({
  play: jest.fn(),
  pause: jest.fn(),
  cancel: jest.fn(),
  currentTime: 0,
  effect: {
    getComputedTiming: () => ({ duration: 10000 }),
  },
}))

// Mock ResizeObserver
class MockResizeObserver {
  observe = jest.fn()
  unobserve = jest.fn()
  disconnect = jest.fn()
}

global.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver

// Mock requestAnimationFrame
global.requestAnimationFrame = jest.fn(() => 1)

global.cancelAnimationFrame = jest.fn()

describe('useHorizontalGallery', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockPrefersReducedMotion = false
    Element.prototype.animate = mockAnimate as any
  })

  describe('initialization', () => {
    it('returns expected shape', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      expect(result.current).toEqual({
        containerRef: expect.any(Object),
        trackRef: expect.any(Object),
        selectedImageId: null,
        handleImageClick: expect.any(Function),
        handleContainerClick: expect.any(Function),
        pauseForKeyboard: expect.any(Function),
        resumeFromKeyboard: expect.any(Function),
        isKeyboardMode: false,
        applySelectionState: expect.any(Function),
        clearSelection: expect.any(Function),
      })
    })

    it('initializes with no selected image', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(result.current.selectedImageId).toBeNull()
    })

    it('initializes with keyboard mode off', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(result.current.isKeyboardMode).toBe(false)
    })

    it('provides containerRef', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(result.current.containerRef).toHaveProperty('current')
    })

    it('provides trackRef', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(result.current.trackRef).toHaveProperty('current')
    })
  })

  describe('handleImageClick', () => {
    it('sets selected image id when clicking an image', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.handleImageClick('image-1')
      })

      expect(result.current.selectedImageId).toBe('image-1')
    })

    it('updates selected image when clicking different image', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.handleImageClick('image-1')
      })
      expect(result.current.selectedImageId).toBe('image-1')

      act(() => {
        result.current.handleImageClick('image-2')
      })
      expect(result.current.selectedImageId).toBe('image-2')
    })

    it('clears selection when clicking same image again', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.handleImageClick('image-1')
      })
      expect(result.current.selectedImageId).toBe('image-1')

      act(() => {
        result.current.handleImageClick('image-1')
      })
      expect(result.current.selectedImageId).toBeNull()
    })
  })

  describe('clearSelection', () => {
    it('clears selected image', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.handleImageClick('image-1')
      })
      expect(result.current.selectedImageId).toBe('image-1')

      act(() => {
        result.current.clearSelection()
      })
      expect(result.current.selectedImageId).toBeNull()
    })

    it('does not affect keyboard mode', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.pauseForKeyboard()
        result.current.handleImageClick('image-1')
      })
      expect(result.current.isKeyboardMode).toBe(true)

      act(() => {
        result.current.clearSelection()
      })
      // clearSelection only clears the selection, not keyboard mode
      expect(result.current.isKeyboardMode).toBe(true)
      expect(result.current.selectedImageId).toBeNull()
    })

    it('can be called when nothing is selected', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      // Should not throw
      act(() => {
        result.current.clearSelection()
      })
      expect(result.current.selectedImageId).toBeNull()
    })
  })

  describe('keyboard navigation mode', () => {
    it('pauseForKeyboard enables keyboard mode', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      expect(result.current.isKeyboardMode).toBe(false)

      act(() => {
        result.current.pauseForKeyboard()
      })

      expect(result.current.isKeyboardMode).toBe(true)
    })

    it('resumeFromKeyboard disables keyboard mode', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.pauseForKeyboard()
      })
      expect(result.current.isKeyboardMode).toBe(true)

      act(() => {
        result.current.resumeFromKeyboard()
      })
      expect(result.current.isKeyboardMode).toBe(false)
    })

    it('resumeFromKeyboard does not clear existing selection', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.pauseForKeyboard()
        result.current.handleImageClick('image-1')
      })
      expect(result.current.selectedImageId).toBe('image-1')

      act(() => {
        result.current.resumeFromKeyboard()
      })
      // resumeFromKeyboard only exits keyboard mode, it does not clear selection
      // This allows user to keep an image selected after exiting keyboard nav
      expect(result.current.selectedImageId).toBe('image-1')
      expect(result.current.isKeyboardMode).toBe(false)
    })
  })

  describe('applySelectionState', () => {
    it('can be called with null', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      // Should not throw
      act(() => {
        result.current.applySelectionState(null)
      })
    })

    it('can be called with an element', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      const element = document.createElement('div')

      // Should not throw
      act(() => {
        result.current.applySelectionState(element)
      })
    })
  })

  describe('handleContainerClick', () => {
    it('is a function', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(typeof result.current.handleContainerClick).toBe('function')
    })

    it('can be called with a mouse event', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      const mockEvent = {
        target: document.createElement('div'),
        preventDefault: jest.fn(),
        stopPropagation: jest.fn(),
      } as unknown as React.MouseEvent

      // Should not throw
      act(() => {
        result.current.handleContainerClick(mockEvent)
      })
    })

    it('clears selection when clicking outside an image', () => {
      const { result } = renderHook(() => useHorizontalGallery())

      // Select an image first
      act(() => {
        result.current.handleImageClick('image-1')
      })
      expect(result.current.selectedImageId).toBe('image-1')

      // Click on container (not on an image)
      const containerDiv = document.createElement('div')
      const mockEvent = {
        target: containerDiv,
        preventDefault: jest.fn(),
        stopPropagation: jest.fn(),
      } as unknown as React.MouseEvent

      act(() => {
        result.current.handleContainerClick(mockEvent)
      })

      // Selection should be cleared
      expect(result.current.selectedImageId).toBeNull()
    })
  })

  describe('options', () => {
    it('accepts scrollSpeed option', () => {
      const { result } = renderHook(() =>
        useHorizontalGallery({ scrollSpeed: 50 })
      )
      expect(result.current).toBeDefined()
    })

    it('accepts enabled option', () => {
      const { result } = renderHook(() =>
        useHorizontalGallery({ enabled: false })
      )
      expect(result.current).toBeDefined()
    })

    it('accepts topRowRef and bottomRowRef options', () => {
      const topRowRef = { current: document.createElement('div') }
      const bottomRowRef = { current: document.createElement('div') }

      const { result } = renderHook(() =>
        useHorizontalGallery({ topRowRef, bottomRowRef })
      )
      expect(result.current).toBeDefined()
    })

    it('uses default values when no options provided', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(result.current).toBeDefined()
    })
  })

  describe('reduced motion', () => {
    it('respects prefers-reduced-motion', () => {
      mockPrefersReducedMotion = true

      const { result } = renderHook(() => useHorizontalGallery())

      // Hook should still return valid shape even with reduced motion
      expect(result.current.containerRef).toBeDefined()
      expect(result.current.trackRef).toBeDefined()
      expect(typeof result.current.handleImageClick).toBe('function')
    })

    it('selection still works with reduced motion', () => {
      mockPrefersReducedMotion = true

      const { result } = renderHook(() => useHorizontalGallery())

      act(() => {
        result.current.handleImageClick('image-1')
      })

      expect(result.current.selectedImageId).toBe('image-1')
    })
  })

  describe('cleanup', () => {
    it('cleans up on unmount', () => {
      const { unmount } = renderHook(() => useHorizontalGallery())

      // Should not throw on unmount
      expect(() => unmount()).not.toThrow()
    })

    it('cleans up when enabled changes to false', () => {
      const { rerender } = renderHook(
        ({ enabled }) => useHorizontalGallery({ enabled }),
        { initialProps: { enabled: true } }
      )

      // Should not throw when disabled
      expect(() => rerender({ enabled: false })).not.toThrow()
    })

    it('handles multiple mount/unmount cycles', () => {
      const { unmount: unmount1 } = renderHook(() => useHorizontalGallery())
      unmount1()

      const { unmount: unmount2 } = renderHook(() => useHorizontalGallery())
      unmount2()

      const { result, unmount: unmount3 } = renderHook(() => useHorizontalGallery())
      expect(result.current).toBeDefined()
      unmount3()
    })
  })

  describe('refs', () => {
    it('containerRef starts as null', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(result.current.containerRef.current).toBeNull()
    })

    it('trackRef starts as null', () => {
      const { result } = renderHook(() => useHorizontalGallery())
      expect(result.current.trackRef.current).toBeNull()
    })
  })
})
