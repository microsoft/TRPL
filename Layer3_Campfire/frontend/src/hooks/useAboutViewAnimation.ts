'use client'

import { useRef, useEffect, useLayoutEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { gsap } from 'gsap'
import { getPrefersReducedMotion } from './usePrefersReducedMotion'
import { setInAppNavFlag } from './useHomeViewAnimation'
import { PAGE_ANIMATION } from '@/lib/constants'

const { EASE_DEFAULT, EASE_EXIT, EASE_INOUT, TRANSITION } = PAGE_ANIMATION

/**
 * useAboutViewAnimation - Entrance and leave animations for the about page
 *
 * Entrance: Slide-up for content
 * Leave: Slide up + fade out transition back to home page
 */
export function useAboutViewAnimation() {
  const router = useRouter()

  // Element refs
  const containerRef = useRef<HTMLElement | null>(null)
  const headerRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const backgroundOverlayRef = useRef<HTMLDivElement | null>(null)

  // Animation state refs
  const hasAnimated = useRef(false)
  const entranceTimelineRef = useRef<gsap.core.Timeline | null>(null)
  const leaveTimelineRef = useRef<gsap.core.Timeline | null>(null)
  const isAnimatingRef = useRef(false)

  // ============================================================================
  // Entrance Animation (useLayoutEffect prevents flash before first paint)
  // ============================================================================

  useLayoutEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    // Set initial states
    if (backgroundOverlayRef.current) {
      gsap.set(backgroundOverlayRef.current, { opacity: 1 })
    }
    if (contentRef.current) {
      gsap.set(contentRef.current, { opacity: 0, y: 200 })
    }

    // Create timeline
    const tl = gsap.timeline({ defaults: { ease: EASE_DEFAULT } })
    entranceTimelineRef.current = tl

    // Fade out background overlay
    if (backgroundOverlayRef.current) {
      tl.to(backgroundOverlayRef.current, {
        opacity: 0,
        duration: 0.6,
        ease: EASE_INOUT,
      }, 0)
    }

    // Animate content - slide up with fade (in parallel)
    if (contentRef.current) {
      tl.to(contentRef.current, {
        opacity: 1,
        y: 0,
        duration: 0.6,
      }, 0)
    }

    // Cleanup
    return () => {
      if (entranceTimelineRef.current) {
        entranceTimelineRef.current.kill()
        entranceTimelineRef.current = null
      }
      hasAnimated.current = false
    }
  }, [])

  // ============================================================================
  // Leave Animation (transition to home)
  // ============================================================================

  const navigateToHome = useCallback(() => {
    setInAppNavFlag()
    router.push('/', { scroll: false })
  }, [router])

  const triggerLeaveAnimation = useCallback(async () => {
    if (getPrefersReducedMotion()) {
      navigateToHome()
      return
    }

    if (isAnimatingRef.current) return
    isAnimatingRef.current = true

    leaveTimelineRef.current?.kill()

    const tl = gsap.timeline({
      onComplete: () => {
        isAnimatingRef.current = false
        leaveTimelineRef.current = null
        navigateToHome()
      },
    })
    leaveTimelineRef.current = tl

    // 1. Start fading background to white
    if (backgroundOverlayRef.current) {
      tl.to(backgroundOverlayRef.current, {
        opacity: 1,
        duration: TRANSITION.BACKGROUND_DURATION,
        ease: EASE_INOUT,
      })
    }

    // 2. Slide header up + fade out
    if (headerRef.current) {
      tl.to(headerRef.current, {
        y: TRANSITION.HERO_OFFSET_Y,
        autoAlpha: 0,
        duration: TRANSITION.HERO_DURATION,
        ease: EASE_EXIT,
      }, '-=0.5')
    }

    // 3. Slide content up + fade out
    if (contentRef.current) {
      tl.to(contentRef.current, {
        y: TRANSITION.CONTENT_OFFSET_Y,
        autoAlpha: 0,
        duration: TRANSITION.CONTENT_DURATION,
        ease: EASE_EXIT,
      }, '<')
    }
  }, [navigateToHome])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      leaveTimelineRef.current?.kill()
      leaveTimelineRef.current = null
      isAnimatingRef.current = false
    }
  }, [])

  return {
    containerRef,
    headerRef,
    contentRef,
    backgroundOverlayRef,
    triggerLeaveAnimation,
  }
}
