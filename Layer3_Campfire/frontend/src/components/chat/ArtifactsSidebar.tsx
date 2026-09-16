// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useRef, useEffect, useLayoutEffect, RefObject } from 'react'
import { gsap } from 'gsap'
import { InlineIcon } from '@/components/ui'
import { getPrefersReducedMotion } from '@/hooks/usePrefersReducedMotion'
import { ArtifactGallery } from '@/components/gallery/ArtifactGallery'
import { GALLERY_IMAGES, PAGE_ANIMATION } from '@/lib/constants'
import styles from './ArtifactsSidebar.module.css'

interface ArtifactsSidebarProps {
  isOpen: boolean
  onToggle: () => void
  /** Optional ref for parent to control leave animations */
  sidebarRef?: RefObject<HTMLDivElement | null>
}

/**
 * ArtifactsSidebar - Right side panel for displaying artifacts/gallery
 * Includes the decorative ripped paper background and toggle button
 * Slides in from the right on mount, toggles via GSAP animation
 */
export function ArtifactsSidebar({ isOpen, onToggle, sidebarRef: externalSidebarRef }: ArtifactsSidebarProps) {
  const internalSidebarRef = useRef<HTMLDivElement>(null)
  // Use external ref if provided, otherwise use internal ref
  // Both are stable refs, so this assignment is safe
  const sidebarRef = externalSidebarRef ?? internalSidebarRef
  const toggleRef = useRef<HTMLButtonElement>(null)
  const timelineRef = useRef<gsap.core.Timeline | null>(null)
  const hasAnimated = useRef(false)
  const isInitialMount = useRef(true)

  // Slide-in animation on mount
  useEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    if (getPrefersReducedMotion()) {
      // Skip animation - show immediately
      if (sidebarRef.current) {
        gsap.set(sidebarRef.current, { x: 0 })
      }
      if (toggleRef.current) {
        gsap.set(toggleRef.current, { opacity: 1 })
      }
      isInitialMount.current = false
      return
    }

    // Animate sidebar sliding in from right
    const tl = gsap.timeline({
      onComplete: () => {
        isInitialMount.current = false
      }
    })
    timelineRef.current = tl

    if (sidebarRef.current) {
      tl.fromTo(
        sidebarRef.current,
        { x: '100%' },
        { x: 0, duration: 0.6, ease: 'power2.out' }
      )
    }

    // Fade in toggle button after sidebar slides in
    if (toggleRef.current) {
      tl.fromTo(
        toggleRef.current,
        { opacity: 0 },
        { opacity: 1, duration: 0.3, ease: 'power2.out' },
        '-=0.2'
      )
    }

    // Cleanup: kill timeline and reset state for React StrictMode compatibility
    return () => {
      if (timelineRef.current) {
        timelineRef.current.kill()
        timelineRef.current = null
      }
      // Reset hasAnimated so animation can re-run if component remounts (StrictMode)
      hasAnimated.current = false
      isInitialMount.current = true
    }
  }, [sidebarRef])

  // Toggle animation - slide in/out based on isOpen state
  // useLayoutEffect prevents flash - runs before browser paint
  useLayoutEffect(() => {
    // Skip toggle animation on initial mount (entrance animation handles it)
    if (isInitialMount.current) return
    if (!sidebarRef.current) return

    // Calculate how much to slide out: element width minus the visible portion
    const sidebarWidth = sidebarRef.current.offsetWidth
    const closedX = sidebarWidth - PAGE_ANIMATION.SIDEBAR.CLOSED_VISIBLE_WIDTH

    if (getPrefersReducedMotion()) {
      gsap.set(sidebarRef.current, { x: isOpen ? 0 : closedX })
      return
    }

    gsap.to(sidebarRef.current, {
      x: isOpen ? 0 : closedX,
      duration: 0.4,
      ease: 'power2.inOut'
    })
  }, [isOpen, sidebarRef])

  return (
    <>
      {/* Sidebar with ripped paper background and gallery content */}
      <div
        ref={sidebarRef}
        className={styles.sidebar}
        aria-hidden={!isOpen}
        inert={!isOpen || undefined}
      >
        <div className={styles.content}>
          <ArtifactGallery images={GALLERY_IMAGES} />
        </div>
      </div>

      {/* Toggle button */}
      <button
        ref={toggleRef}
        className={`${styles.toggle} ${isOpen ? styles.toggleOpen : ''}`}
        onClick={onToggle}
        aria-label={isOpen ? 'Hide artifacts' : 'Show artifacts'}
        aria-expanded={isOpen}
        tabIndex={0}
      >
        <span className={`${styles.toggleText} ${isOpen ? styles.toggleTextOpen : ''}`}>
          {isOpen ? 'Hide Artifacts' : 'Show Artifacts'}
        </span>
        <InlineIcon
          name="chevron-down"
          size={12}
          className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ''}`}
        />
      </button>
    </>
  )
}
