// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useRef, useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { gsap } from 'gsap'
import { TextPlugin } from 'gsap/TextPlugin'
import { usePrefersReducedMotion, getPrefersReducedMotion } from './usePrefersReducedMotion'
import { applyHomeLeaveTransitionFallback } from './animationFallback'
import { logger } from '@/lib/logger'
import { PAGE_ANIMATION } from '@/lib/constants'

// Register GSAP plugins
gsap.registerPlugin(TextPlugin)

const { HOME, TRANSITION, IN_APP, EASE_DEFAULT, EASE_INOUT, EASE_EXIT } = PAGE_ANIMATION

// Session storage key for in-app navigation detection
const IN_APP_NAV_KEY = 'reading-room-in-app-nav'

/**
 * Check the in-app navigation flag from sessionStorage (read-only, render-safe)
 */
function checkInAppNavFlag(): boolean {
  if (typeof window === 'undefined') return false
  return sessionStorage.getItem(IN_APP_NAV_KEY) === 'true'
}

/**
 * Clear the in-app navigation flag from sessionStorage (side effect, effect-only)
 */
function clearInAppNavFlag(): void {
  if (typeof window === 'undefined') return
  sessionStorage.removeItem(IN_APP_NAV_KEY)
}

/**
 * Set the in-app navigation flag in sessionStorage
 */
export function setInAppNavFlag(): void {
  if (typeof window === 'undefined') return
  sessionStorage.setItem(IN_APP_NAV_KEY, 'true')
}

interface UseHomeViewAnimationOptions {
  /** Whether critical assets have finished loading */
  isAssetsReady?: boolean
}

/**
 * useHomeViewAnimation - Orchestrated entrance and leave animations for the home page
 *
 * Entrance: Typewriter effect for headline and staggered reveal of content
 * Leave: Slide up + fade out transition to chat page
 */
export const useHomeViewAnimation = ({ isAssetsReady = true }: UseHomeViewAnimationOptions = {}) => {
  const router = useRouter()

  // Element refs
  const containerRef = useRef<HTMLDivElement | null>(null)
  const taglineRef = useRef<HTMLParagraphElement | null>(null)
  const titleRef = useRef<HTMLHeadingElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const footerRef = useRef<HTMLDivElement | null>(null)
  const galleryRef = useRef<HTMLDivElement | null>(null)
  const heroRef = useRef<HTMLDivElement | null>(null)
  const backgroundOverlayRef = useRef<HTMLDivElement | null>(null)
  const loadingScreenRef = useRef<HTMLDivElement | null>(null)

  // Animation state refs
  const leaveTimelineRef = useRef<gsap.core.Timeline | null>(null)
  const entranceTimelineRef = useRef<gsap.core.Timeline | null>(null)
  const isAnimatingRef = useRef(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasStartedEntranceRef = useRef(false)

  const taglineText = 'Discover the Life & Legacy of'
  const titleText = 'Theodore Roosevelt'

  const prefersReducedMotion = usePrefersReducedMotion()

  // ============================================================================
  // Determine Animation Type (read ONCE during render, persists through StrictMode)
  // ============================================================================
  // This ref is initialized to null and only set once during the first render.
  // Because refs persist through React StrictMode's double-mount cycle,
  // both mounts will see the same animation type.
  const animationTypeRef = useRef<'none' | 'in-app' | 'theatrical' | null>(null)
  if (animationTypeRef.current === null) {
    if (prefersReducedMotion) {
      animationTypeRef.current = 'none'
    } else if (checkInAppNavFlag()) {
      animationTypeRef.current = 'in-app'
    } else {
      animationTypeRef.current = 'theatrical'
    }
  }

  // ============================================================================
  // Clear in-app navigation flag (side effect isolated from render)
  // ============================================================================
  useEffect(() => {
    if (animationTypeRef.current === 'in-app') {
      clearInAppNavFlag()
    }
  }, [])

  // ============================================================================
  // In-App Navigation Animation (simple fade-in + slide-up)
  // Uses useEffect instead of useGSAP for more control
  // No need to wait for assets - they're already cached from initial visit
  // ============================================================================
  useEffect(() => {
    if (animationTypeRef.current !== 'in-app') return
    if (!containerRef.current) return

    // Set text content immediately
    if (taglineRef.current) taglineRef.current.textContent = taglineText
    if (titleRef.current) titleRef.current.textContent = titleText

    // Hide loading screen immediately for in-app navigation
    if (loadingScreenRef.current) gsap.set(loadingScreenRef.current, { autoAlpha: 0 })

    // Set initial states
    if (backgroundOverlayRef.current) gsap.set(backgroundOverlayRef.current, { opacity: 1 })
    if (taglineRef.current) gsap.set(taglineRef.current, { visibility: 'visible', opacity: 0, y: IN_APP.OFFSET_Y })
    if (titleRef.current) gsap.set(titleRef.current, { visibility: 'visible', opacity: 0, y: IN_APP.OFFSET_Y })
    if (contentRef.current) gsap.set(contentRef.current, { autoAlpha: 0, y: IN_APP.OFFSET_Y })
    if (galleryRef.current) gsap.set(galleryRef.current, { opacity: 0, visibility: 'visible' })
    if (footerRef.current) {
      gsap.set(footerRef.current, { visibility: 'visible' })
      gsap.set(footerRef.current.children, { autoAlpha: 0, y: IN_APP.OFFSET_Y })
    }

    // Create a context for cleanup
    const ctx = gsap.context(() => {
      // Animate everything in parallel
      gsap.to(backgroundOverlayRef.current, { opacity: 0, duration: IN_APP.DURATION, ease: EASE_INOUT })
      gsap.to(taglineRef.current, { opacity: 1, y: 0, duration: IN_APP.DURATION, ease: EASE_DEFAULT })
      gsap.to(titleRef.current, { opacity: 1, y: 0, duration: IN_APP.DURATION, ease: EASE_DEFAULT })
      gsap.to(contentRef.current, { autoAlpha: 1, y: 0, duration: IN_APP.DURATION, ease: EASE_DEFAULT })
      gsap.to(galleryRef.current, { opacity: 1, duration: IN_APP.DURATION, ease: EASE_INOUT })
      if (footerRef.current?.children.length) {
        gsap.to(footerRef.current.children, { autoAlpha: 1, y: 0, duration: IN_APP.DURATION, ease: EASE_DEFAULT })
      }
    }, containerRef)

    return () => ctx.revert()
  }, []) // Empty deps - only run once

  // ============================================================================
  // Theatrical Entrance Animation (typewriter + staggered reveals)
  // Waits for assets to load before starting
  // ============================================================================
  useEffect(() => {
    if (animationTypeRef.current !== 'theatrical') return
    if (!containerRef.current) return
    if (!isAssetsReady) return
    if (hasStartedEntranceRef.current) return
    hasStartedEntranceRef.current = true

    // Respect user's reduced motion preference - show final state immediately
    if (prefersReducedMotion) {
      if (taglineRef.current) {
        taglineRef.current.textContent = taglineText
        gsap.set(taglineRef.current, { visibility: 'visible' })
      }
      if (titleRef.current) {
        titleRef.current.textContent = titleText
        gsap.set(titleRef.current, { visibility: 'visible' })
      }
      if (contentRef.current) gsap.set(contentRef.current, { autoAlpha: 1, y: 0 })
      if (galleryRef.current) gsap.set(galleryRef.current, { opacity: 1, visibility: 'visible' })
      if (footerRef.current) {
        gsap.set(footerRef.current, { visibility: 'visible' })
        gsap.set(footerRef.current.children, { autoAlpha: 1, y: 0 })
      }
      if (backgroundOverlayRef.current) gsap.set(backgroundOverlayRef.current, { opacity: 0 })
      if (loadingScreenRef.current) gsap.set(loadingScreenRef.current, { autoAlpha: 0 })
      return
    }

    const ctx = gsap.context(() => {
      const timeline = gsap.timeline({ defaults: { ease: EASE_DEFAULT } })
      entranceTimelineRef.current = timeline

      // Immediately hide elements that animate later (prevents flash)
      if (contentRef.current) gsap.set(contentRef.current, { autoAlpha: 0, y: HOME.CONTENT_OFFSET_Y })
      if (footerRef.current) gsap.set(footerRef.current.children, { autoAlpha: 0, y: HOME.FOOTER_OFFSET_Y })
      if (galleryRef.current) gsap.set(galleryRef.current, { opacity: 0, visibility: 'visible' })

      // Fade out loading screen first
      if (loadingScreenRef.current) {
        timeline.to(loadingScreenRef.current, { autoAlpha: 0, duration: 0.4, ease: EASE_INOUT })
      }

      // Tagline typewriter animation
      if (taglineRef.current) {
        taglineRef.current.textContent = ''
        gsap.set(taglineRef.current, { visibility: 'visible' })
        timeline.to(taglineRef.current, { duration: HOME.TAGLINE_DURATION, text: { value: taglineText, delimiter: '' }, ease: 'none' })
      }

      // Title typewriter animation (waits for tagline to complete)
      if (titleRef.current) {
        titleRef.current.textContent = ''
        gsap.set(titleRef.current, { visibility: 'visible' })
        timeline.to(titleRef.current, { duration: HOME.TITLE_DURATION, text: { value: titleText, delimiter: '' }, ease: 'none' })
      }

      // Content slide-in animation (waits for title to complete)
      if (contentRef.current) {
        timeline.to(contentRef.current, { autoAlpha: 1, y: 0, duration: HOME.CONTENT_DURATION })
      }

      // Gallery fade-in (parallel with content)
      if (galleryRef.current) {
        timeline.to(galleryRef.current, { opacity: 1, duration: HOME.GALLERY_DURATION, ease: EASE_INOUT }, `-=${HOME.CONTENT_DURATION}`)
      }

      // Footer buttons staggered animation
      if (footerRef.current?.children.length) {
        gsap.set(footerRef.current, { visibility: 'visible' })
        timeline.to(footerRef.current.children, { autoAlpha: 1, y: 0, duration: HOME.FOOTER_DURATION, stagger: HOME.FOOTER_STAGGER }, `-=${HOME.FOOTER_DURATION}`)
      }
    }, containerRef)

    return () => {
      ctx.revert()
      entranceTimelineRef.current = null
      hasStartedEntranceRef.current = false
    }
  }, [isAssetsReady, prefersReducedMotion])

  // ============================================================================
  // Reduced Motion Handler (sets final state immediately)
  // ============================================================================
  useEffect(() => {
    if (!prefersReducedMotion) return
    if (!containerRef.current) return

    if (taglineRef.current) {
      taglineRef.current.textContent = taglineText
      gsap.set(taglineRef.current, { visibility: 'visible' })
    }
    if (titleRef.current) {
      titleRef.current.textContent = titleText
      gsap.set(titleRef.current, { visibility: 'visible' })
    }
    if (contentRef.current) gsap.set(contentRef.current, { autoAlpha: 1, y: 0 })
    if (galleryRef.current) gsap.set(galleryRef.current, { opacity: 1, visibility: 'visible' })
    if (footerRef.current) {
      gsap.set(footerRef.current, { visibility: 'visible' })
      gsap.set(footerRef.current.children, { autoAlpha: 1, y: 0 })
    }
    if (backgroundOverlayRef.current) gsap.set(backgroundOverlayRef.current, { opacity: 0 })
    if (loadingScreenRef.current) gsap.set(loadingScreenRef.current, { autoAlpha: 0 })
  }, [prefersReducedMotion])

  // ============================================================================
  // Leave Animation (transition to chat)
  // ============================================================================

  const clearTimeoutRef = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }
  }, [])

  const getRefs = useCallback(() => ({
    heroRef,
    contentRef,
    galleryRef,
    footerRef,
    backgroundOverlayRef,
  }), [])

  const triggerLeaveAnimation = useCallback(async (destination: string = '/chat') => {
    if (getPrefersReducedMotion()) {
      router.push(destination, { scroll: false })
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

      // 2. Slide headline upward + off page (parallel with background)
      if (heroRef.current) {
        tl.to(heroRef.current, {
          y: TRANSITION.HERO_OFFSET_Y,
          autoAlpha: 0,
          duration: TRANSITION.HERO_DURATION,
          ease: EASE_EXIT,
        }, '-=0.7')
      }

      // 3. Slide content (chat box) upward + off page
      if (contentRef.current) {
        tl.to(
          contentRef.current,
          {
            y: TRANSITION.CONTENT_OFFSET_Y,
            autoAlpha: 0,
            duration: TRANSITION.CONTENT_DURATION,
            ease: EASE_EXIT,
          },
          `-=${TRANSITION.CONTENT_DURATION}`
        )
      }

      // 4. Slide footer buttons upward + off page
      if (footerRef.current) {
        tl.to(
          footerRef.current,
          {
            y: TRANSITION.FOOTER_OFFSET_Y,
            autoAlpha: 0,
            duration: TRANSITION.FOOTER_DURATION,
            ease: EASE_EXIT,
          },
          `-=${TRANSITION.FOOTER_DURATION}`
        )
      }

      // 5. Fade out gallery
      if (galleryRef.current) {
        tl.to(
          galleryRef.current,
          {
            autoAlpha: 0,
            duration: TRANSITION.GALLERY_DURATION,
            ease: EASE_EXIT,
          },
          `-=${TRANSITION.GALLERY_DURATION}`
        )
      }

      const timelinePromise = tl.then().then(() => 'timeline' as const)
      const timeoutPromise = new Promise<'timeout' | void>((resolve) => {
        timeoutRef.current = setTimeout(() => {
          if (!isAnimatingRef.current) return resolve()
          leaveTimelineRef.current?.kill()
          applyHomeLeaveTransitionFallback(getRefs())
          isAnimatingRef.current = false
          leaveTimelineRef.current = null
          router.push(destination, { scroll: false })
          resolve('timeout')
        }, TRANSITION.SAFETY_TIMEOUT)
      })

      const winner = await Promise.race([timelinePromise, timeoutPromise])

      if (winner === 'timeline') {
        clearTimeoutRef()
        router.push(destination, { scroll: false })
      }
    } catch (error) {
      logger.error('Home leave animation transition failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      })
      isAnimatingRef.current = false
      leaveTimelineRef.current?.kill()
      leaveTimelineRef.current = null
      clearTimeoutRef()
      applyHomeLeaveTransitionFallback(getRefs())
      router.push(destination, { scroll: false })
    }
  }, [clearTimeoutRef, router, getRefs])

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
    containerRef,
    taglineRef,
    titleRef,
    contentRef,
    footerRef,
    galleryRef,
    heroRef,
    backgroundOverlayRef,
    loadingScreenRef,
    // Leave animation trigger
    triggerLeaveAnimation,
  }
}
