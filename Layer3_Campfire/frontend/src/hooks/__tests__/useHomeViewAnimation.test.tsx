// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { renderHook, act } from '@testing-library/react'

// Mock next/navigation
const mockPush = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))

jest.mock('gsap', () => {
  // Mock GSAP functions
  const mockGsapSet = jest.fn()
  const mockGsapTo = jest.fn()
  const mockTimelineTo = jest.fn().mockReturnThis()
  const mockTimelineThen = jest.fn(() => Promise.resolve())
  const mockTimelineKill = jest.fn()

  const createMockTimeline = (opts?: { onComplete?: () => void }) => ({
    to: mockTimelineTo,
    then: mockTimelineThen,
    kill: mockTimelineKill,
    onComplete: opts?.onComplete,
  })

  const mockGsapTimeline = jest.fn(createMockTimeline)

  return {
    __esModule: true,
    gsap: {
      registerPlugin: jest.fn(),
      set: mockGsapSet,
      to: mockGsapTo,
      timeline: mockGsapTimeline,
    },
  }
})

// Get access to mocks
const { gsap } = jest.requireMock('gsap')
const mockGsapSet = gsap.set
const mockGsapTo = gsap.to
const mockGsapTimeline = gsap.timeline

// Initialize timeline mocks
let mockTimelineTo: jest.Mock = jest.fn().mockReturnThis()
let mockTimelineThen: jest.Mock = jest.fn(() => Promise.resolve())
let mockTimelineKill: jest.Mock = jest.fn()

jest.mock('gsap/TextPlugin', () => ({
  TextPlugin: {},
}))

// Mock reduced motion - default to false
let mockPrefersReducedMotion = false
jest.mock('../usePrefersReducedMotion', () => ({
  usePrefersReducedMotion: () => mockPrefersReducedMotion,
  getPrefersReducedMotion: () => mockPrefersReducedMotion,
}))

// Mock sessionStorage
const mockSessionStorage: Record<string, string> = {}
Object.defineProperty(window, 'sessionStorage', {
  value: {
    getItem: jest.fn((key: string) => mockSessionStorage[key] || null),
    setItem: jest.fn((key: string, value: string) => {
      mockSessionStorage[key] = value
    }),
    removeItem: jest.fn((key: string) => {
      delete mockSessionStorage[key]
    }),
  },
  writable: true,
})

import { useHomeViewAnimation, setInAppNavFlag } from '../useHomeViewAnimation'

describe('useHomeViewAnimation', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    mockPrefersReducedMotion = false
    // Clear sessionStorage mock
    Object.keys(mockSessionStorage).forEach((key) => delete mockSessionStorage[key])

    // Set up the timeline mocks to capture instances when created
    mockGsapTimeline.mockImplementation((opts?: { onComplete?: () => void }) => {
      const timeline = {
        to: jest.fn().mockReturnThis(),
        then: jest.fn(() => Promise.resolve()),
        kill: jest.fn(),
        onComplete: opts?.onComplete,
      }
      mockTimelineTo = timeline.to
      mockTimelineThen = timeline.then
      mockTimelineKill = timeline.kill
      return timeline
    })
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  describe('initialization', () => {
    it('returns expected refs and functions', () => {
      const { result } = renderHook(() => useHomeViewAnimation())

      expect(result.current).toEqual({
        containerRef: expect.any(Object),
        taglineRef: expect.any(Object),
        titleRef: expect.any(Object),
        contentRef: expect.any(Object),
        footerRef: expect.any(Object),
        galleryRef: expect.any(Object),
        heroRef: expect.any(Object),
        backgroundOverlayRef: expect.any(Object),
        loadingScreenRef: expect.any(Object),
        triggerLeaveAnimation: expect.any(Function),
      })
    })

    it('all refs are initially null', () => {
      const { result } = renderHook(() => useHomeViewAnimation())

      expect(result.current.containerRef.current).toBeNull()
      expect(result.current.taglineRef.current).toBeNull()
      expect(result.current.titleRef.current).toBeNull()
      expect(result.current.contentRef.current).toBeNull()
      expect(result.current.footerRef.current).toBeNull()
      expect(result.current.galleryRef.current).toBeNull()
      expect(result.current.heroRef.current).toBeNull()
      expect(result.current.backgroundOverlayRef.current).toBeNull()
      expect(result.current.loadingScreenRef.current).toBeNull()
    })
  })

  describe('triggerLeaveAnimation', () => {
    it('navigates to /chat immediately when reduced motion is preferred', async () => {
      mockPrefersReducedMotion = true
      const { result } = renderHook(() => useHomeViewAnimation())

      await act(async () => {
        await result.current.triggerLeaveAnimation()
      })

      expect(mockPush).toHaveBeenCalledWith('/chat', { scroll: false })
    })

    it('prevents re-entry while animating', async () => {
      mockPrefersReducedMotion = false
      // Make timeline never resolve
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))

      const { result } = renderHook(() => useHomeViewAnimation())

      // Start first animation
      act(() => {
        result.current.triggerLeaveAnimation()
      })

      const initialCallCount = mockGsapTimeline.mock.calls.length

      // Try to start second animation
      act(() => {
        result.current.triggerLeaveAnimation()
      })

      // Only one timeline should be created (no additional calls)
      expect(mockGsapTimeline.mock.calls.length).toBe(initialCallCount)
    })

    it('navigates after animation completes', async () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => Promise.resolve())

      const { result } = renderHook(() => useHomeViewAnimation())

      await act(async () => {
        await result.current.triggerLeaveAnimation()
      })

      expect(mockPush).toHaveBeenCalledWith('/chat', { scroll: false })
    })

    it('applies safety timeout fallback', async () => {
      mockPrefersReducedMotion = false
      // Timeline never resolves
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))

      const { result } = renderHook(() => useHomeViewAnimation())

      act(() => {
        result.current.triggerLeaveAnimation()
      })

      // Advance past safety timeout (2000ms)
      act(() => {
        jest.advanceTimersByTime(2100)
      })

      expect(mockTimelineKill).toHaveBeenCalled()
      expect(mockPush).toHaveBeenCalledWith('/chat', { scroll: false })
    })
  })

  describe('cleanup', () => {
    it('does not throw on unmount', () => {
      const { unmount } = renderHook(() => useHomeViewAnimation())
      expect(() => unmount()).not.toThrow()
    })

    it('kills timeline on unmount', () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))

      const { result, unmount } = renderHook(() => useHomeViewAnimation())

      act(() => {
        result.current.triggerLeaveAnimation()
      })

      unmount()

      expect(mockTimelineKill).toHaveBeenCalled()
    })
  })
})

describe('setInAppNavFlag', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('sets flag in sessionStorage', () => {
    setInAppNavFlag()
    expect(window.sessionStorage.setItem).toHaveBeenCalledWith(
      'reading-room-in-app-nav',
      'true'
    )
  })
})
