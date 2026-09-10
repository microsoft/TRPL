'use client'

import { useRef, useEffect, useLayoutEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { gsap } from 'gsap'
import { getPrefersReducedMotion } from './usePrefersReducedMotion'
import { applyChatLeaveTransitionFallback } from './animationFallback'
import { setInAppNavFlag } from './useHomeViewAnimation'
import { logger } from '@/lib/logger'
import { PAGE_ANIMATION } from '@/lib/constants'

const { CHAT, TRANSITION, EASE_ENTRANCE, EASE_EXIT, EASE_INOUT } = PAGE_ANIMATION

/**
 * useChatViewAnimation - Entrance and leave animations for the chat view
 *
 * Entrance: Content slides up into place on mount
 * Leave: Slide up + fade out transition back to home page
 */
export function useChatViewAnimation() {
  const router = useRouter()

  // Element refs
  const headerRef = useRef<HTMLDivElement>(null)
  const chatAreaRef = useRef<HTMLDivElement>(null)
  const chatbarRef = useRef<HTMLDivElement>(null)
  const artifactsSidebarRef = useRef<HTMLDivElement>(null)
  const backgroundOverlayRef = useRef<HTMLDivElement>(null)

  // Animation state refs
  const entranceTimelineRef = useRef<gsap.core.Timeline | null>(null)
  const leaveTimelineRef = useRef<gsap.core.Timeline | null>(null)
  const hasAnimated = useRef(false)
  const isAnimatingRef = useRef(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ============================================================================
  // Entrance Animation (useLayoutEffect prevents flash before first paint)
  // ============================================================================

  useLayoutEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    if (getPrefersReducedMotion()) {
      // Skip animation - show immediately
      if (backgroundOverlayRef.current) {
        gsap.set(backgroundOverlayRef.current, { opacity: 0 })
      }
      if (headerRef.current) {
        gsap.set(headerRef.current, { opacity: 1 })
      }
      if (chatAreaRef.current) {
        gsap.set(chatAreaRef.current, { opacity: 1, y: 0 })
      }
      if (chatbarRef.current) {
        gsap.set(chatbarRef.current, { opacity: 1, y: 0 })
      }
      return
    }

    // Set initial states immediately (prevents flash of content)
    if (headerRef.current) {
      gsap.set(headerRef.current, { opacity: 0 })
    }
    if (chatAreaRef.current) {
      gsap.set(chatAreaRef.current, { opacity: 0, y: CHAT.CHAT_AREA_OFFSET_Y })
    }
    if (chatbarRef.current) {
      gsap.set(chatbarRef.current, { opacity: 0, y: CHAT.CHATBAR_OFFSET_Y })
    }

    const tl = gsap.timeline({ defaults: { ease: EASE_ENTRANCE } })
    entranceTimelineRef.current = tl

    // All animations start at position 0 to run in parallel

    // 0. Fade out background overlay
    if (backgroundOverlayRef.current) {
      tl.to(backgroundOverlayRef.current, { opacity: 0, duration: 0.6, ease: EASE_INOUT }, 0)
    }

    // 1. Fade in back button
    if (headerRef.current) {
      tl.to(headerRef.current, { opacity: 1, duration: CHAT.BACK_BUTTON_ENTRANCE_DURATION, ease: 'none' }, 0)
    }

    // 2. Slide up chat area (slide and opacity separated for different easing)
    if (chatAreaRef.current) {
      // Slide uses power3.out (fast start, slow end) - feels natural
      tl.to(chatAreaRef.current, { y: 0, duration: CHAT.ENTRANCE_DURATION }, 0)
      // Opacity uses linear - gradual fade throughout
      tl.to(chatAreaRef.current, { opacity: 1, duration: CHAT.ENTRANCE_DURATION, ease: 'none' }, 0)
    }

    // 3. Slide up chatbar from bottom with fade (in parallel)
    if (chatbarRef.current) {
      tl.to(chatbarRef.current, { y: 0, duration: CHAT.ENTRANCE_DURATION }, 0)
      tl.to(chatbarRef.current, { opacity: 1, duration: CHAT.ENTRANCE_DURATION, ease: 'none' }, 0)
    }

    // Cleanup: kill timeline and reset state for React StrictMode compatibility
    return () => {
      if (entranceTimelineRef.current) {
        entranceTimelineRef.current.kill()
        entranceTimelineRef.current = null
      }
      // Reset hasAnimated so animation can re-run if component remounts (StrictMode)
      hasAnimated.current = false
    }
  }, [])

  // ============================================================================
  // Leave Animation (transition to home)
  // ============================================================================

  const clearTimeoutRef = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }
  }, [])

  const getRefs = useCallback(() => ({
    headerRef,
    chatAreaRef,
    chatbarRef,
    artifactsSidebarRef,
    backgroundOverlayRef,
  }), [])

  // Helper to navigate with in-app navigation flag
  const navigateTo = useCallback((destination: string = '/') => {
    setInAppNavFlag()
    router.push(destination, { scroll: false })
  }, [router])

  const triggerLeaveAnimation = useCallback(async (onBeforeNavigateOrDestination?: (() => void) | string) => {
    const onBeforeNavigate = typeof onBeforeNavigateOrDestination === 'function' ? onBeforeNavigateOrDestination : undefined
    const destination = typeof onBeforeNavigateOrDestination === 'string' ? onBeforeNavigateOrDestination : '/'

    if (getPrefersReducedMotion()) {
      onBeforeNavigate?.()
      navigateTo(destination)
      return
    }

    if (isAnimatingRef.current) return
    isAnimatingRef.current = true

    leaveTimelineRef.current?.kill()

    try {
      const tl = gsap.timeline({
        onComplete: () => {
          isAnimatingRef.current = false
          leaveTimelineRef.current = null
          clearTimeoutRef()
        },
      })
      leaveTimelineRef.current = tl

      // 1. Start fading background to white (runs throughout transition)
      if (backgroundOverlayRef.current) {
        tl.to(backgroundOverlayRef.current, {
          opacity: 1,
          duration: TRANSITION.BACKGROUND_DURATION,
          ease: EASE_INOUT,
        })
      }

      // 2. Slide back button upward + fade out
      if (headerRef.current) {
        tl.to(headerRef.current, {
          y: TRANSITION.HERO_OFFSET_Y,
          autoAlpha: 0,
          duration: TRANSITION.HERO_DURATION,
          ease: EASE_EXIT,
        }, '-=0.7')
      }

      // 3. Slide chat area upward + fade out (starts same time as back button)
      if (chatAreaRef.current) {
        // Use separate y and opacity tweens for consistent behavior with entrance animation
        tl.to(chatAreaRef.current, { y: TRANSITION.CONTENT_OFFSET_Y, duration: TRANSITION.CONTENT_DURATION, ease: EASE_EXIT }, '<')
        tl.to(chatAreaRef.current, { opacity: 0, duration: TRANSITION.CONTENT_DURATION, ease: 'none' }, '<')
      }

      // 4. Slide chatbar upward + fade out (starts same time as back button)
      if (chatbarRef.current) {
        tl.to(chatbarRef.current, { y: TRANSITION.FOOTER_OFFSET_Y, duration: TRANSITION.FOOTER_DURATION, ease: EASE_EXIT }, '<')
        tl.to(chatbarRef.current, { opacity: 0, duration: TRANSITION.FOOTER_DURATION, ease: 'none' }, '<')
      }

      // 5. Slide artifacts sidebar out to the right (starts same time as back button)
      if (artifactsSidebarRef.current) {
        tl.to(artifactsSidebarRef.current, { x: '100%', duration: TRANSITION.CONTENT_DURATION, ease: EASE_EXIT }, '<')
      }

      const timelinePromise = tl.then().then(() => 'timeline' as const)
      const timeoutPromise = new Promise<'timeout' | void>((resolve) => {
        timeoutRef.current = setTimeout(() => {
          if (!isAnimatingRef.current) return resolve()
          leaveTimelineRef.current?.kill()
          applyChatLeaveTransitionFallback(getRefs())
          isAnimatingRef.current = false
          leaveTimelineRef.current = null
          onBeforeNavigate?.()
          navigateTo(destination)
          resolve('timeout')
        }, TRANSITION.SAFETY_TIMEOUT)
      })

      const winner = await Promise.race([timelinePromise, timeoutPromise])

      if (winner === 'timeline') {
        clearTimeoutRef()
        onBeforeNavigate?.()
        navigateTo(destination)
      }
    } catch (error) {
      logger.error('Chat leave animation transition failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      })
      isAnimatingRef.current = false
      leaveTimelineRef.current?.kill()
      leaveTimelineRef.current = null
      clearTimeoutRef()
      applyChatLeaveTransitionFallback(getRefs())
      onBeforeNavigate?.()
      navigateTo(destination)
    }
  }, [clearTimeoutRef, navigateTo, getRefs])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      leaveTimelineRef.current?.kill()
      leaveTimelineRef.current = null
      isAnimatingRef.current = false
      clearTimeoutRef()
    }
  }, [clearTimeoutRef])

  return {
    // Element refs
    headerRef,
    chatAreaRef,
    chatbarRef,
    artifactsSidebarRef,
    backgroundOverlayRef,
    // Leave animation trigger
    triggerLeaveAnimation,
  }
}
