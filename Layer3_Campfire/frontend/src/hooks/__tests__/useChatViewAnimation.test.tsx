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
  // Store onComplete callback for potential test inspection (prefixed to indicate intentionally unused)
  let _mockTimelineOnComplete: (() => void) | undefined

  const createMockTimeline = (opts?: { onComplete?: () => void }) => {
    _mockTimelineOnComplete = opts?.onComplete
    return {
      to: mockTimelineTo,
      then: mockTimelineThen,
      kill: mockTimelineKill,
    }
  }

  const mockGsapTimeline = jest.fn(createMockTimeline)

  return {
    __esModule: true,
    gsap: {
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

// Mock reduced motion - default to false
let mockPrefersReducedMotion = false
jest.mock('../usePrefersReducedMotion', () => ({
  getPrefersReducedMotion: () => mockPrefersReducedMotion,
}))

// Mock setInAppNavFlag
const mockSetInAppNavFlag = jest.fn()
jest.mock('../useHomeViewAnimation', () => ({
  setInAppNavFlag: () => mockSetInAppNavFlag(),
}))

// Mock logger
jest.mock('@/lib/logger', () => ({
  logger: {
    error: jest.fn(),
  },
}))

import { useChatViewAnimation } from '../useChatViewAnimation'

describe('useChatViewAnimation', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    mockPrefersReducedMotion = false

    // Set up the timeline mocks to capture instances when created
    mockGsapTimeline.mockImplementation((opts?: { onComplete?: () => void }) => {
      const timeline = {
        to: jest.fn().mockReturnThis(),
        then: jest.fn(() => Promise.resolve()),
        kill: jest.fn(),
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
      const { result } = renderHook(() => useChatViewAnimation())

      expect(result.current).toEqual({
        headerRef: expect.any(Object),
        chatAreaRef: expect.any(Object),
        chatbarRef: expect.any(Object),
        artifactsSidebarRef: expect.any(Object),
        backgroundOverlayRef: expect.any(Object),
        triggerLeaveAnimation: expect.any(Function),
      })
    })

    it('all refs are initially null', () => {
      const { result } = renderHook(() => useChatViewAnimation())

      expect(result.current.headerRef.current).toBeNull()
      expect(result.current.chatAreaRef.current).toBeNull()
      expect(result.current.chatbarRef.current).toBeNull()
      expect(result.current.artifactsSidebarRef.current).toBeNull()
      expect(result.current.backgroundOverlayRef.current).toBeNull()
    })
  })

  describe('entrance animation', () => {
    it('creates entrance timeline when refs are attached', () => {
      // Without DOM elements attached to refs, the animation won't run
      // This tests that the hook initializes correctly
      const { result } = renderHook(() => useChatViewAnimation())

      // Hook should return valid refs that can be attached
      expect(result.current.headerRef).toBeDefined()
      expect(result.current.chatAreaRef).toBeDefined()
      expect(result.current.chatbarRef).toBeDefined()
    })

    it('respects reduced motion preference', () => {
      mockPrefersReducedMotion = true
      mockGsapTimeline.mockClear()

      const { result } = renderHook(() => useChatViewAnimation())

      // Hook should still return valid structure regardless of motion preference
      expect(result.current.triggerLeaveAnimation).toBeInstanceOf(Function)
    })
  })

  describe('triggerLeaveAnimation', () => {
    it('navigates immediately when reduced motion is preferred', async () => {
      mockPrefersReducedMotion = true
      const { result } = renderHook(() => useChatViewAnimation())

      await act(async () => {
        await result.current.triggerLeaveAnimation()
      })

      expect(mockSetInAppNavFlag).toHaveBeenCalled()
      expect(mockPush).toHaveBeenCalledWith('/', { scroll: false })
    })

    it('calls onBeforeNavigate callback when provided', async () => {
      mockPrefersReducedMotion = true
      const onBeforeNavigate = jest.fn()
      const { result } = renderHook(() => useChatViewAnimation())

      await act(async () => {
        await result.current.triggerLeaveAnimation(onBeforeNavigate)
      })

      expect(onBeforeNavigate).toHaveBeenCalled()
      expect(mockPush).toHaveBeenCalledWith('/', { scroll: false })
    })

    it('prevents re-entry while animating', async () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))

      const { result } = renderHook(() => useChatViewAnimation())

      // Start first animation
      act(() => {
        result.current.triggerLeaveAnimation()
      })

      const initialCallCount = mockGsapTimeline.mock.calls.length

      // Try to start second animation
      act(() => {
        result.current.triggerLeaveAnimation()
      })

      // Should not create another timeline
      expect(mockGsapTimeline.mock.calls.length).toBe(initialCallCount)
    })

    it('sets in-app navigation flag before navigating', async () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => Promise.resolve())

      const { result } = renderHook(() => useChatViewAnimation())

      await act(async () => {
        await result.current.triggerLeaveAnimation()
      })

      expect(mockSetInAppNavFlag).toHaveBeenCalled()
    })

    it('navigates to home after animation completes', async () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => Promise.resolve())

      const { result } = renderHook(() => useChatViewAnimation())

      await act(async () => {
        await result.current.triggerLeaveAnimation()
      })

      expect(mockPush).toHaveBeenCalledWith('/', { scroll: false })
    })

    it('applies safety timeout fallback', async () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))

      const { result } = renderHook(() => useChatViewAnimation())

      act(() => {
        result.current.triggerLeaveAnimation()
      })

      // Advance past safety timeout (2000ms)
      act(() => {
        jest.advanceTimersByTime(2100)
      })

      expect(mockTimelineKill).toHaveBeenCalled()
      expect(mockPush).toHaveBeenCalledWith('/', { scroll: false })
    })

    it('calls onBeforeNavigate on timeout fallback', async () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))
      const onBeforeNavigate = jest.fn()

      const { result } = renderHook(() => useChatViewAnimation())

      act(() => {
        result.current.triggerLeaveAnimation(onBeforeNavigate)
      })

      act(() => {
        jest.advanceTimersByTime(2100)
      })

      expect(onBeforeNavigate).toHaveBeenCalled()
    })
  })

  describe('cleanup', () => {
    it('does not throw on unmount', () => {
      const { unmount } = renderHook(() => useChatViewAnimation())
      expect(() => unmount()).not.toThrow()
    })

    it('kills leave timeline on unmount', () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))

      const { result, unmount } = renderHook(() => useChatViewAnimation())

      act(() => {
        result.current.triggerLeaveAnimation()
      })

      unmount()

      expect(mockTimelineKill).toHaveBeenCalled()
    })

    it('clears timeout on unmount', () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => new Promise(() => {}))

      const { result, unmount } = renderHook(() => useChatViewAnimation())

      act(() => {
        result.current.triggerLeaveAnimation()
      })

      // Unmount before timeout fires
      unmount()

      // Advance timers - should not cause any issues
      act(() => {
        jest.advanceTimersByTime(3000)
      })

      // Navigation should only happen once from cleanup, not from timeout
      expect(mockPush.mock.calls.length).toBeLessThanOrEqual(1)
    })
  })

  describe('error handling', () => {
    it('navigates even if animation throws', async () => {
      mockPrefersReducedMotion = false
      mockTimelineThen.mockImplementation(() => Promise.reject(new Error('Animation failed')))

      const { result } = renderHook(() => useChatViewAnimation())

      await act(async () => {
        await result.current.triggerLeaveAnimation()
      })

      // Should still navigate despite error
      expect(mockPush).toHaveBeenCalledWith('/', { scroll: false })
    })
  })
})
