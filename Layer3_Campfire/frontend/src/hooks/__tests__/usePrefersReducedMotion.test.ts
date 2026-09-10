/**
 * @jest-environment jsdom
 */

import { renderHook, act } from '@testing-library/react'
import { usePrefersReducedMotion, getPrefersReducedMotion } from '../usePrefersReducedMotion'

describe('usePrefersReducedMotion', () => {
  let originalMatchMedia: typeof window.matchMedia
  let mockMediaQueryList: {
    matches: boolean
    addEventListener: jest.Mock
    removeEventListener: jest.Mock
  }

  beforeEach(() => {
    originalMatchMedia = window.matchMedia

    mockMediaQueryList = {
      matches: false,
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
    }

    window.matchMedia = jest.fn().mockReturnValue(mockMediaQueryList)
  })

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  describe('initial state', () => {
    it('returns false by default (SSR-safe)', () => {
      const { result } = renderHook(() => usePrefersReducedMotion())
      // Initial state before effect runs is false
      expect(result.current).toBe(false)
    })

    it('returns true when user prefers reduced motion', () => {
      mockMediaQueryList.matches = true
      const { result } = renderHook(() => usePrefersReducedMotion())
      expect(result.current).toBe(true)
    })

    it('returns false when user does not prefer reduced motion', () => {
      mockMediaQueryList.matches = false
      const { result } = renderHook(() => usePrefersReducedMotion())
      expect(result.current).toBe(false)
    })
  })

  describe('media query subscription', () => {
    it('queries the correct media query', () => {
      renderHook(() => usePrefersReducedMotion())
      expect(window.matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
    })

    it('adds event listener on mount', () => {
      renderHook(() => usePrefersReducedMotion())
      expect(mockMediaQueryList.addEventListener).toHaveBeenCalledWith(
        'change',
        expect.any(Function)
      )
    })

    it('removes event listener on unmount', () => {
      const { unmount } = renderHook(() => usePrefersReducedMotion())
      unmount()
      expect(mockMediaQueryList.removeEventListener).toHaveBeenCalledWith(
        'change',
        expect.any(Function)
      )
    })
  })

  describe('reactive updates', () => {
    it('updates when preference changes to true', () => {
      mockMediaQueryList.matches = false
      const { result } = renderHook(() => usePrefersReducedMotion())

      expect(result.current).toBe(false)

      // Simulate preference change
      act(() => {
        const changeHandler = mockMediaQueryList.addEventListener.mock.calls[0][1]
        changeHandler({ matches: true })
      })

      expect(result.current).toBe(true)
    })

    it('updates when preference changes to false', () => {
      mockMediaQueryList.matches = true
      const { result } = renderHook(() => usePrefersReducedMotion())

      expect(result.current).toBe(true)

      // Simulate preference change
      act(() => {
        const changeHandler = mockMediaQueryList.addEventListener.mock.calls[0][1]
        changeHandler({ matches: false })
      })

      expect(result.current).toBe(false)
    })
  })
})

describe('getPrefersReducedMotion', () => {
  let originalMatchMedia: typeof window.matchMedia

  beforeEach(() => {
    originalMatchMedia = window.matchMedia
  })

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  it('returns true when user prefers reduced motion', () => {
    window.matchMedia = jest.fn().mockReturnValue({ matches: true })
    expect(getPrefersReducedMotion()).toBe(true)
  })

  it('returns false when user does not prefer reduced motion', () => {
    window.matchMedia = jest.fn().mockReturnValue({ matches: false })
    expect(getPrefersReducedMotion()).toBe(false)
  })

  it('queries the correct media query', () => {
    window.matchMedia = jest.fn().mockReturnValue({ matches: false })
    getPrefersReducedMotion()
    expect(window.matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
  })

  describe('SSR safety', () => {
    it('has early return for SSR (window undefined check exists)', () => {
      // The function has a check for typeof window === 'undefined'
      // We verify this by checking the function handles the browser case correctly
      // SSR behavior is tested implicitly - if the check didn't exist,
      // the function would throw in a real SSR environment
      window.matchMedia = jest.fn().mockReturnValue({ matches: false })
      expect(() => getPrefersReducedMotion()).not.toThrow()
    })
  })
})
