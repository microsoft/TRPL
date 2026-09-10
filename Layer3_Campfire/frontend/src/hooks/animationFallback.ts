'use client'

import gsap from 'gsap'
import { RefObject } from 'react'
import { PAGE_ANIMATION } from '@/lib/constants'

const { TRANSITION } = PAGE_ANIMATION

// ============================================================================
// Home Page Leave Transition Fallback
// ============================================================================

interface HomeLeaveTransitionRefs {
  heroRef: RefObject<HTMLDivElement | null>
  contentRef: RefObject<HTMLDivElement | null>
  galleryRef: RefObject<HTMLDivElement | null>
  footerRef: RefObject<HTMLDivElement | null>
  backgroundOverlayRef: RefObject<HTMLDivElement | null>
}

/**
 * applyHomeLeaveTransitionFallback
 *
 * Ensures the home-to-chat transition ends in a visually consistent state when
 * animations fail, are cancelled, or time out. This should NOT change the
 * appearance when animations succeed; it is only invoked as a safety net.
 */
export const applyHomeLeaveTransitionFallback = (refs: HomeLeaveTransitionRefs) => {
  const { heroRef, contentRef, galleryRef, footerRef, backgroundOverlayRef } = refs

  if (backgroundOverlayRef.current) {
    gsap.set(backgroundOverlayRef.current, { opacity: 1 })
  }

  if (heroRef.current) {
    gsap.set(heroRef.current, { y: TRANSITION.HERO_OFFSET_Y, autoAlpha: 0 })
  }

  if (contentRef.current) {
    gsap.set(contentRef.current, { y: TRANSITION.CONTENT_OFFSET_Y, autoAlpha: 0 })
  }

  if (footerRef.current) {
    gsap.set(footerRef.current, { y: TRANSITION.FOOTER_OFFSET_Y, autoAlpha: 0 })
  }

  if (galleryRef.current) {
    gsap.set(galleryRef.current, { autoAlpha: 0 })
  }
}

// ============================================================================
// Chat Page Leave Transition Fallback
// ============================================================================

interface ChatLeaveTransitionRefs {
  headerRef: RefObject<HTMLDivElement | null>
  chatAreaRef: RefObject<HTMLDivElement | null>
  chatbarRef: RefObject<HTMLDivElement | null>
  artifactsSidebarRef: RefObject<HTMLDivElement | null>
  backgroundOverlayRef: RefObject<HTMLDivElement | null>
}

/**
 * applyChatLeaveTransitionFallback
 *
 * Ensures the chat-to-home transition ends in a visually consistent state when
 * animations fail, are cancelled, or time out. This should NOT change the
 * appearance when animations succeed; it is only invoked as a safety net.
 */
export const applyChatLeaveTransitionFallback = (refs: ChatLeaveTransitionRefs) => {
  const { headerRef, chatAreaRef, chatbarRef, artifactsSidebarRef, backgroundOverlayRef } = refs

  if (backgroundOverlayRef.current) {
    gsap.set(backgroundOverlayRef.current, { opacity: 1 })
  }

  if (headerRef.current) {
    gsap.set(headerRef.current, { y: TRANSITION.HERO_OFFSET_Y, autoAlpha: 0 })
  }

  if (chatAreaRef.current) {
    gsap.set(chatAreaRef.current, { y: TRANSITION.CONTENT_OFFSET_Y, opacity: 0 })
  }

  if (chatbarRef.current) {
    gsap.set(chatbarRef.current, { y: TRANSITION.FOOTER_OFFSET_Y, opacity: 0 })
  }

  if (artifactsSidebarRef.current) {
    gsap.set(artifactsSidebarRef.current, { x: '100%' })
  }
}
