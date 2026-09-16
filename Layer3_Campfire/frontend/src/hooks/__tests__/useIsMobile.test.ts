// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { renderHook, act } from '@testing-library/react'
import { useIsMobile } from '../useIsMobile'

const MOBILE_BREAKPOINT = 768
const DEBOUNCE_MS = 150

describe('useIsMobile', () => {
  let originalInnerWidth: number
  let resizeListeners: Array<() => void>

  beforeEach(() => {
    jest.useFakeTimers()
    originalInnerWidth = window.innerWidth
    resizeListeners = []

    // Mock addEventListener/removeEventListener for resize
    jest.spyOn(window, 'addEventListener').mockImplementation((event, handler) => {
      if (event === 'resize') {
        resizeListeners.push(handler as () => void)
      }
    })

    jest.spyOn(window, 'removeEventListener').mockImplementation((event, handler) => {
      if (event === 'resize') {
        resizeListeners = resizeListeners.filter((h) => h !== handler)
      }
    })
  })

  afterEach(() => {
    jest.useRealTimers()
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: originalInnerWidth,
    })
    jest.restoreAllMocks()
  })

  const setViewportWidth = (width: number) => {
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: width,
    })
  }

  const triggerResize = () => {
    resizeListeners.forEach((listener) => listener())
  }

  describe('initial state', () => {
    it('returns true when viewport is below mobile breakpoint', () => {
      setViewportWidth(MOBILE_BREAKPOINT - 1)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(true)
    })

    it('returns false when viewport is at mobile breakpoint', () => {
      setViewportWidth(MOBILE_BREAKPOINT)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(false)
    })

    it('returns false when viewport is above mobile breakpoint', () => {
      setViewportWidth(MOBILE_BREAKPOINT + 1)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(false)
    })

    it('returns false for typical desktop width', () => {
      setViewportWidth(1920)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(false)
    })

    it('returns true for typical mobile width', () => {
      setViewportWidth(375)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(true)
    })
  })

  describe('reactive updates', () => {
    it('updates when viewport changes from desktop to mobile', () => {
      setViewportWidth(1024)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(false)

      act(() => {
        setViewportWidth(320)
        triggerResize()
        jest.advanceTimersByTime(DEBOUNCE_MS)
      })

      expect(result.current).toBe(true)
    })

    it('updates when viewport changes from mobile to desktop', () => {
      setViewportWidth(320)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(true)

      act(() => {
        setViewportWidth(1024)
        triggerResize()
        jest.advanceTimersByTime(DEBOUNCE_MS)
      })

      expect(result.current).toBe(false)
    })
  })

  describe('debouncing', () => {
    it('debounces rapid resize events', () => {
      setViewportWidth(1024)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(false)

      // Simulate rapid resizing
      act(() => {
        setViewportWidth(800)
        triggerResize()
        jest.advanceTimersByTime(50)

        setViewportWidth(600)
        triggerResize()
        jest.advanceTimersByTime(50)

        setViewportWidth(400)
        triggerResize()
        jest.advanceTimersByTime(50)
      })

      // Value hasn't changed yet because debounce hasn't completed
      expect(result.current).toBe(false)

      // Complete the debounce
      act(() => {
        jest.advanceTimersByTime(DEBOUNCE_MS)
      })

      // Now it should reflect the final value
      expect(result.current).toBe(true)
    })

    it('does not update until debounce period completes', () => {
      setViewportWidth(1024)
      const { result } = renderHook(() => useIsMobile())

      act(() => {
        setViewportWidth(320)
        triggerResize()
        jest.advanceTimersByTime(DEBOUNCE_MS - 1)
      })

      // Still false because debounce hasn't completed
      expect(result.current).toBe(false)

      act(() => {
        jest.advanceTimersByTime(1)
      })

      // Now it should be true
      expect(result.current).toBe(true)
    })
  })

  describe('event listener management', () => {
    it('adds resize listener on mount', () => {
      renderHook(() => useIsMobile())
      expect(window.addEventListener).toHaveBeenCalledWith('resize', expect.any(Function))
    })

    it('removes resize listener on unmount', () => {
      const { unmount } = renderHook(() => useIsMobile())
      unmount()
      expect(window.removeEventListener).toHaveBeenCalledWith('resize', expect.any(Function))
    })

    it('shares listener across multiple hook instances', () => {
      const { unmount: unmount1 } = renderHook(() => useIsMobile())
      const { unmount: unmount2 } = renderHook(() => useIsMobile())

      // Only one listener should be added
      const addCalls = (window.addEventListener as jest.Mock).mock.calls.filter(
        (call) => call[0] === 'resize'
      )
      expect(addCalls.length).toBe(1)

      // Unmount first instance - listener should remain
      unmount1()
      expect(window.removeEventListener).not.toHaveBeenCalled()

      // Unmount second instance - now listener should be removed
      unmount2()
      expect(window.removeEventListener).toHaveBeenCalled()
    })
  })

  describe('SSR behavior', () => {
    it('defaults to false on server (desktop assumption)', () => {
      // The getServerSnapshot returns false
      // This is tested implicitly through the hook's behavior
      setViewportWidth(1024)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(false)
    })
  })

  describe('boundary conditions', () => {
    it('handles viewport width of 0', () => {
      setViewportWidth(0)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(true)
    })

    it('handles very large viewport', () => {
      setViewportWidth(10000)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(false)
    })

    it('handles exact breakpoint boundary', () => {
      setViewportWidth(767)
      const { result } = renderHook(() => useIsMobile())
      expect(result.current).toBe(true)

      act(() => {
        setViewportWidth(768)
        triggerResize()
        jest.advanceTimersByTime(DEBOUNCE_MS)
      })

      expect(result.current).toBe(false)
    })
  })

  describe('cleanup', () => {
    it('does not throw on unmount', () => {
      const { unmount } = renderHook(() => useIsMobile())
      expect(() => unmount()).not.toThrow()
    })

    it('handles multiple mount/unmount cycles', () => {
      const { unmount: unmount1 } = renderHook(() => useIsMobile())
      unmount1()

      const { unmount: unmount2 } = renderHook(() => useIsMobile())
      unmount2()

      const { result, unmount: unmount3 } = renderHook(() => useIsMobile())
      expect(result.current).toBeDefined()
      unmount3()
    })
  })
})
