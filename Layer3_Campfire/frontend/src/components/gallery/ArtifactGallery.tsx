// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useRef, useCallback, memo } from 'react'
import styles from './ArtifactGallery.module.css'
import clsx from 'clsx'

// ============================================================================
// Types
// ============================================================================

export interface ArtifactImage {
  id: string
  src: string
  alt: string
}

interface ArtifactGalleryProps {
  images: readonly ArtifactImage[]
  className?: string
  onImageClick?: (image: ArtifactImage) => void
}

// ============================================================================
// Main Component
// ============================================================================

/**
 * ArtifactGallery - Single-column scrollable gallery for artifacts
 * - Fixed 200px width images with natural height
 * - 14px gap between images
 * - Manual scroll/drag only (no autoscroll)
 */
export const ArtifactGallery = memo(function ArtifactGallery({
  images,
  className,
  onImageClick,
}: ArtifactGalleryProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const isDraggingRef = useRef(false)
  const startYRef = useRef(0)
  const scrollTopRef = useRef(0)

  // ============================================================================
  // Drag to Scroll Handlers
  // ============================================================================

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    const container = containerRef.current
    if (!container) return

    isDraggingRef.current = true
    startYRef.current = e.clientY
    scrollTopRef.current = container.scrollTop
    container.style.cursor = 'grabbing'
    container.style.userSelect = 'none'
  }, [])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isDraggingRef.current) return
    const container = containerRef.current
    if (!container) return

    const deltaY = e.clientY - startYRef.current
    container.scrollTop = scrollTopRef.current - deltaY
  }, [])

  const handleMouseUp = useCallback(() => {
    isDraggingRef.current = false
    const container = containerRef.current
    if (container) {
      container.style.cursor = 'grab'
      container.style.userSelect = ''
    }
  }, [])

  const handleMouseLeave = useCallback(() => {
    if (isDraggingRef.current) {
      handleMouseUp()
    }
  }, [handleMouseUp])

  // Handle image click (only if not dragging)
  const handleImageClick = useCallback((image: ArtifactImage, e: React.MouseEvent) => {
    // Prevent click if we were dragging
    if (isDraggingRef.current) {
      e.preventDefault()
      return
    }
    onImageClick?.(image)
  }, [onImageClick])

  // ============================================================================
  // Render
  // ============================================================================

  if (!images || images.length === 0) {
    return (
      <div
        ref={containerRef}
        className={clsx(styles.container, className)}
        role="region"
        aria-label="Artifact gallery"
      >
        <p className={styles.emptyMessage}>No artifacts to display</p>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className={clsx(styles.container, className)}
      role="region"
      aria-label="Artifact gallery"
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseLeave}
    >
      <div className={styles.column}>
        {images.map((image) => (
          <div
            key={image.id}
            className={styles.imageWrapper}
            onClick={(e) => handleImageClick(image, e)}
            role="button"
            tabIndex={0}
            aria-label={image.alt}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onImageClick?.(image)
              }
            }}
          >
            <img
              src={image.src}
              alt={image.alt}
              className={styles.image}
              loading="lazy"
              decoding="async"
              draggable={false}
            />
          </div>
        ))}
      </div>
    </div>
  )
})
