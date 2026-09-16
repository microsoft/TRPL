// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useState, useEffect } from 'react'
import { HeroSection } from '@/components/home'
import { Gallery } from '@/components/gallery'
import { Button } from '@/components/ui'
import { LoadingScreen } from '@/components/common/LoadingScreen'
import { ReportIssueModal } from '@/components/chat/ReportIssueModal'
import { useAssetLoader } from '@/hooks/useAssetLoader'
import { useHomeViewAnimation } from '@/hooks/useHomeViewAnimation'
import { useSessionStore } from '@/stores/sessionStore'
import {
  GALLERY_ARTIFACTS,
  GALLERY_IMAGES,
  HOME_CRITICAL_ASSET_PATHS,
  getHomeGalleryArtifactRoute,
} from '@/lib/constants'
import type { GalleryImage } from '@/components/gallery/VerticalGallery'
import styles from './HomeView.module.css'

/**
 * HomeView - Main view component for the home page
 * Handles GSAP animations and interactive elements
 */
export function HomeView() {
  const [reportModalOpen, setReportModalOpen] = useState(false)
  const { isReady: isAssetsReady } = useAssetLoader(HOME_CRITICAL_ASSET_PATHS)
  const resetSession = useSessionStore((state) => state.resetSession)

  // Clear any previous chat session when the homepage mounts.
  // This is the "fresh start" boundary — navigating here (via back button,
  // browser back, direct URL, etc.) always begins a clean session.
  useEffect(() => {
    resetSession()
  }, [resetSession])

  const {
    containerRef,
    taglineRef,
    titleRef,
    contentRef,
    footerRef,
    galleryRef,
    heroRef,
    backgroundOverlayRef,
    loadingScreenRef,
    triggerLeaveAnimation,
  } = useHomeViewAnimation({ isAssetsReady })

  const handleArtifactClick = (image: GalleryImage) => {
    const route = getHomeGalleryArtifactRoute(image.id)
    if (route) {
      triggerLeaveAnimation(route)
    }
  }

  return (
    <>
      {/* Loading screen - rendered outside main to avoid stacking context issues */}
      <LoadingScreen ref={loadingScreenRef} isVisible={!isAssetsReady} />

      <main ref={containerRef} className={styles.main}>
        <div className={styles.layout}>
          {/* Hero Section */}
          <section className={styles.hero}>
            <HeroSection
              taglineRef={taglineRef}
              titleRef={titleRef}
              contentRef={contentRef}
              heroRef={heroRef}
              onSubmit={triggerLeaveAnimation}
            />
          </section>

          {/* Gallery Sidebar - Infinite scroll gallery */}
          <aside ref={galleryRef} className={styles.gallery}>
            <Gallery
              images={[...GALLERY_IMAGES]}
              scrollSpeed={30}
              onImageAction={GALLERY_ARTIFACTS.length ? handleArtifactClick : undefined}
            />
          </aside>

          {/* Footer with action buttons */}
          <footer ref={footerRef} className={styles.footer}>
            <Button variant="primary" href="https://www.trlibrary.com/" target="_blank" rel="noopener noreferrer">
              Theodore Roosevelt Presidential Library
              <svg className={styles.externalLinkIcon} width="10" height="10" viewBox="0 0 10 10" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path d="M3.61411 0.638899C3.61411 0.286051 3.90015 8.7253e-06 4.253 6.30559e-06L8.77064 5.27002e-06C9.12348 7.90793e-06 9.40953 0.28605 9.40953 0.638897L9.40952 5.15652C9.40952 5.50937 9.12347 5.79541 8.77062 5.79541C8.41778 5.79541 8.13173 5.50937 8.13173 5.15652L8.13175 2.18131L1.09066 9.22239C0.841162 9.47189 0.436647 9.47189 0.187145 9.22239C-0.0623565 8.97289 -0.0623698 8.56836 0.187132 8.31886L7.2282 1.27779L4.253 1.27779C3.90015 1.27779 3.61411 0.991744 3.61411 0.638899Z" fill="currentColor"/>
              </svg>
            </Button>
            <Button variant="primary" onClick={() => triggerLeaveAnimation('/about')}>How we use Artificial Intelligence</Button>
            <Button variant="primary" onClick={() => setReportModalOpen(true)}>Report an Issue</Button>
          </footer>
        </div>

        {/* Background overlay for smooth transition to chat page */}
        <div ref={backgroundOverlayRef} className={styles.backgroundOverlay} />
      </main>
      <ReportIssueModal open={reportModalOpen} onOpenChange={setReportModalOpen} />
    </>
  )
}
