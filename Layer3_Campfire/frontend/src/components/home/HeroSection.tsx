// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { RefObject } from 'react'
import { ChatBox, PromptBar } from '@/components/chat'
import styles from './HeroSection.module.css'

interface HeroSectionProps {
  taglineRef: RefObject<HTMLParagraphElement | null>
  titleRef: RefObject<HTMLHeadingElement | null>
  contentRef: RefObject<HTMLDivElement | null>
  heroRef: RefObject<HTMLDivElement | null>
  onSubmit?: () => void
}

/**
 * Hero Section Component
 * Receives refs from parent for GSAP typewriter animations and transitions
 */
export const HeroSection = ({
  taglineRef,
  titleRef,
  contentRef,
  heroRef,
  onSubmit,
}: HeroSectionProps) => {
  return (
    <div className={styles.wrapper}>
      {/* Hero text section - wrapper for transition animation */}
      <div ref={heroRef} className={styles.hero}>
        <p ref={taglineRef} className={styles.tagline}>
          Discover the Life &amp; Legacy of
        </p>
        <h1 ref={titleRef} className={styles.title}>
          Theodore Roosevelt
        </h1>
      </div>

      {/* Chat/Search container - sibling to hero */}
      <div ref={contentRef} className={styles.searchContainer}>
        <ChatBox onSubmit={onSubmit} />
        <PromptBar />
      </div>
    </div>
  )
}
