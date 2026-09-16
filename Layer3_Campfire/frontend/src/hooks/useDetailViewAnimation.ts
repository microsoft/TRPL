// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useRef, useEffect, useLayoutEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { gsap } from 'gsap'
import { getPrefersReducedMotion } from './usePrefersReducedMotion'
import { setInAppNavFlag } from './useHomeViewAnimation'
import { PAGE_ANIMATION } from '@/lib/constants'

const { EASE_DEFAULT, EASE_EXIT, EASE_INOUT, TRANSITION } = PAGE_ANIMATION

/**
 * useDetailViewAnimation - Entrance and leave animations for the artifact detail page
 *
 * Entrance: Background overlay fades out + header/content slide up from below
 * Leave: Slide up + fade out, then navigate back
 */
export function useDetailViewAnimation() {
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

  // ==========================================================================
  // Entrance Animation (useLayoutEffect prevents flash before first paint)
  // ==========================================================================

  useLayoutEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    if (getPrefersReducedMotion()) {
      if (backgroundOverlayRef.current) gsap.set(backgroundOverlayRef.current, { opacity: 0 })
      if (headerRef.current) gsap.set(headerRef.current, { opacity: 1, y: 0 })
      if (contentRef.current) gsap.set(contentRef.current, { opacity: 1, y: 0 })
      return
    }

    // Set initial states
    if (backgroundOverlayRef.current) {
      gsap.set(backgroundOverlayRef.current, { opacity: 1 })
    }
    if (headerRef.current) {
      gsap.set(headerRef.current, { opacity: 0, y: 200 })
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

    // Slide header up + fade in
    if (headerRef.current) {
      tl.to(headerRef.current, {
        opacity: 1,
        y: 0,
        duration: 0.6,
      }, 0)
    }

    // Slide content up + fade in
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

  // ==========================================================================
  // Leave Animation
  // ==========================================================================

  const navigateTo = useCallback((destination: string) => {
    setInAppNavFlag()
    router.push(destination, { scroll: false })
  }, [router])

  const triggerLeaveAnimation = useCallback(async (destination: string = '/') => {
    if (getPrefersReducedMotion()) {
      navigateTo(destination)
      return
    }

    if (isAnimatingRef.current) return
    isAnimatingRef.current = true

    leaveTimelineRef.current?.kill()

    const tl = gsap.timeline({
      onComplete: () => {
        isAnimatingRef.current = false
        leaveTimelineRef.current = null
        navigateTo(destination)
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

    // 3. Slide content up + fade out (parallel with header)
    if (contentRef.current) {
      tl.to(contentRef.current, {
        y: TRANSITION.CONTENT_OFFSET_Y,
        autoAlpha: 0,
        duration: TRANSITION.CONTENT_DURATION,
        ease: EASE_EXIT,
      }, '<')
    }
  }, [navigateTo])

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
