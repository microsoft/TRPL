'use client'

import { useMemo, useRef, memo, useCallback, useState } from 'react'
import { useHorizontalGallery } from '@/hooks/useHorizontalGallery'
import { InlineIcon } from '@/components/ui'
import styles from './HorizontalGallery.module.css'
import clsx from 'clsx'

// ============================================================================
// Types
// ============================================================================

export interface GalleryImage {
  id: string
  src: string
  alt: string
  caption?: string
}

interface HorizontalGalleryProps {
  images: GalleryImage[]
  scrollSpeed?: number
  className?: string
  onImageAction?: (image: GalleryImage) => void
  getHref?: (image: GalleryImage) => string
}

interface GalleryItemProps {
  image: GalleryImage
  href: string
}

interface GalleryRowProps {
  images: GalleryImage[]
  rowRef: React.RefObject<HTMLDivElement | null>
  getHref: (image: GalleryImage) => string
  rowPosition: 'top' | 'bottom'
}

// ============================================================================
// Sub-Components
// ============================================================================

/**
 * GalleryItem - Renders a single image item in the gallery row.
 * Memoized to prevent unnecessary re-renders when props are stable.
 * 
 * Note: Individual images have tabIndex={-1} because keyboard navigation
 * is handled at the container level via programmatic focus management.
 */
const GalleryItem = memo(function GalleryItem({
  image,
  href,
}: GalleryItemProps) {
  return (
    <a
      className={styles.imageWrapper}
      data-gallery-image
      data-image-id={image.id}
      href={href}
      tabIndex={-1}
      aria-label={`View ${image.alt}`}
      onClick={(e) => e.preventDefault()}
      draggable={false}
    >
      <div className={styles.imageContainer}>
        {/*
          Using native <img> instead of next/image intentionally:
          - loading="eager" is required for accurate width measurements
          - WAAPI animations need synchronous dimension access
          - next/image's lazy loading would break initial animation setup
        */}
        <img
          src={image.src}
          alt={image.alt}
          className={styles.image}
          loading="eager"
          decoding="sync"
          draggable={false}
        />
      </div>
      <div className={styles.caption}>
        <span className={styles.captionText}>
          {image.caption || image.alt}
        </span>
        <span className={styles.captionButton} aria-hidden="true">
          <InlineIcon name="arrow-right" size={16} />
        </span>
      </div>
    </a>
  )
})

/**
 * GalleryRow - Renders a single horizontal row of images.
 * Images are rendered 3x (triplicated) for seamless infinite scrolling.
 * Extra duplication prevents blank areas during fast scrolling/dragging.
 * The row container is animated via Web Animations API, not individual items.
 * Selection styling uses DOM data attributes (data-selected, data-dimmed).
 * Memoized to prevent unnecessary re-renders.
 */
const GalleryRow = memo(function GalleryRow({
  images,
  rowRef,
  rowPosition,
  getHref,
}: GalleryRowProps) {
  return (
    <div ref={rowRef} className={styles.row} data-row={rowPosition}>
      {/* First set of images */}
      {images.map((image, index) => (
        <GalleryItem
          key={`${rowPosition}-${image.id}-${index}-a`}
          image={image}
          href={getHref(image)}
        />
      ))}
      {/* Second set for seamless infinite loop */}
      {images.map((image, index) => (
        <GalleryItem
          key={`${rowPosition}-${image.id}-${index}-b`}
          image={image}
          href={getHref(image)}
        />
      ))}
      {/* Third set for fast scroll buffer */}
      {images.map((image, index) => (
        <GalleryItem
          key={`${rowPosition}-${image.id}-${index}-c`}
          image={image}
          href={getHref(image)}
        />
      ))}
    </div>
  )
})

// ============================================================================
// Main Component
// ============================================================================

/**
 * Calculates fully visible images inside a container and returns them sorted by visual position.
 * @internal Exported for unit testing only - not part of public API
 */
export const getFullyVisibleImages = (container: HTMLElement): HTMLElement[] => {
  const allImages = container.querySelectorAll('[data-gallery-image]')
  const containerRect = container.getBoundingClientRect()

  const visible = Array.from(allImages).filter((img): img is HTMLElement => {
    if (!(img instanceof HTMLElement)) return false
    const rect = img.getBoundingClientRect()
    return rect.left >= containerRect.left && rect.right <= containerRect.right
  })

  visible.sort((a, b) => {
    const rectA = a.getBoundingClientRect()
    const rectB = b.getBoundingClientRect()
    if (Math.abs(rectA.top - rectB.top) > 50) {
      return rectA.top - rectB.top
    }
    return rectA.left - rectB.left
  })

  return visible
}

/**
 * HorizontalGallery - Infinite scrolling image gallery using Web Animations API.
 * Designed for mobile/tablet with horizontal scrolling.
 * Uses native element.animate() for compositor-thread animation.
 *
 * @param props - Gallery configuration props
 * @param props.images - Array of images to display
 * @param props.scrollSpeed - Pixels per second for auto-scroll (default: 30)
 * @param props.className - Optional additional CSS classes
 */
export const HorizontalGallery = memo(function HorizontalGallery({ images, scrollSpeed = 30, className, onImageAction, getHref }: HorizontalGalleryProps) {
  const topRowRef = useRef<HTMLDivElement>(null)
  const bottomRowRef = useRef<HTMLDivElement>(null)
  const focusWrapperRef = useRef<HTMLDivElement>(null)

  const defaultGetHref = useCallback((image: GalleryImage) => `/artifact/${encodeURIComponent(image.id)}`, [])
  const resolvedGetHref = getHref ?? defaultGetHref

  const handleSecondClick = useCallback((imageId: string) => {
    const image = images.find((img) => img.id === imageId)
    if (image) onImageAction?.(image)
  }, [images, onImageAction])

  const {
    containerRef,
    trackRef,
    handleContainerClick,
    pauseForKeyboard,
    resumeFromKeyboard,
    isKeyboardMode,
    applySelectionState,
    clearSelection,
  } = useHorizontalGallery({
    scrollSpeed,
    enabled: true,
    topRowRef,
    bottomRowRef,
    onSecondClick: handleSecondClick,
  })

  // Track focused image index for keyboard navigation (null = container focused, not an image)
  const [focusedImageIndex, setFocusedImageIndex] = useState<number | null>(null)
  const visibleImagesRef = useRef<HTMLElement[]>([])

  // ============================================================================
  // Keyboard Navigation Helpers
  // ============================================================================

  /**
   * Calculates which images are fully visible and returns them sorted by visual position.
   * Sort order: top row left-to-right, then bottom row left-to-right.
   */
  const calculateVisibleImages = useCallback((): HTMLElement[] => {
    const container = containerRef.current
    if (!container) return []
    return getFullyVisibleImages(container)
  }, [containerRef])

  /**
   * Handle focus entering the gallery container
   */
  const handleGalleryFocus = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    // Only trigger if focus came from outside the gallery
    const wrapper = focusWrapperRef.current
    if (!wrapper) return
    
    const relatedTarget = e.relatedTarget as Node | null
    const focusCameFromInside = relatedTarget && wrapper.contains(relatedTarget)
    
    if (!focusCameFromInside) {
      // Focus entered from outside - enter keyboard mode and pause animation
      pauseForKeyboard()
      // Calculate visible images now that animation is paused
      visibleImagesRef.current = calculateVisibleImages()
      // Reset focused index - user needs to Tab again to focus first image
      setFocusedImageIndex(null)
    }
  }, [pauseForKeyboard, calculateVisibleImages])

  /**
   * Handle focus leaving the gallery container
   */
  const handleGalleryBlur = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    // Only trigger if focus left the gallery entirely
    const wrapper = focusWrapperRef.current
    if (!wrapper) return
    
    const relatedTarget = e.relatedTarget as Node | null
    const focusStayedInside = relatedTarget && wrapper.contains(relatedTarget)
    
    if (!focusStayedInside) {
      // Focus left the gallery - clear selection, exit keyboard mode and resume animation
      clearSelection()
      resumeFromKeyboard()
      setFocusedImageIndex(null)
      visibleImagesRef.current = []
    }
  }, [clearSelection, resumeFromKeyboard])

  /**
   * Handle keyboard navigation within the gallery
   */
  const handleGalleryKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    // Handle Escape - exit keyboard mode and return focus to wrapper
    if (e.key === 'Escape' && isKeyboardMode) {
      e.preventDefault()
      clearSelection()
      setFocusedImageIndex(null)
      focusWrapperRef.current?.focus()
      return
    }

    // Only handle Tab key in keyboard mode
    if (e.key !== 'Tab' || !isKeyboardMode) return

    const images = visibleImagesRef.current
    if (images.length === 0) return

    const currentIndex = focusedImageIndex

    if (e.shiftKey) {
      // SHIFT+TAB: move backwards
      if (currentIndex === null) {
        // On wrapper, going backwards - clear selection, exit keyboard mode, let browser handle exit
        clearSelection()
        resumeFromKeyboard()
        return
      }
      if (currentIndex === 0) {
        // At first image - go back to wrapper, clear selection
        e.preventDefault()
        clearSelection()
        setFocusedImageIndex(null)
        focusWrapperRef.current?.focus()
      } else {
        // Move to previous image
        e.preventDefault()
        const newIndex = currentIndex - 1
        const targetElement = images[newIndex]
        setFocusedImageIndex(newIndex)
        targetElement?.focus()
        // Apply selection state to show caption (same as click)
        applySelectionState(targetElement)
      }
    } else {
      // TAB: move forward
      if (currentIndex === null) {
        // On wrapper - move to first visible image
        e.preventDefault()
        const targetElement = images[0]
        setFocusedImageIndex(0)
        targetElement?.focus()
        // Apply selection state to show caption (same as click)
        applySelectionState(targetElement)
      } else if (currentIndex >= images.length - 1) {
        // At last image - exit gallery forwards
        // Clear selection, exit keyboard mode, let browser handle Tab
        clearSelection()
        setFocusedImageIndex(null)
        resumeFromKeyboard()
        return
      } else {
        // Move to next image
        e.preventDefault()
        const newIndex = currentIndex + 1
        const targetElement = images[newIndex]
        setFocusedImageIndex(newIndex)
        targetElement?.focus()
        // Apply selection state to show caption (same as click)
        applySelectionState(targetElement)
      }
    }
  }, [isKeyboardMode, focusedImageIndex, resumeFromKeyboard, clearSelection, applySelectionState])

  // Split images into two rows for staggered effect
  // Moved BEFORE early return to comply with React hooks rules
  const { topRow, bottomRow } = useMemo(() => {
    if (!images || images.length === 0) {
      return { topRow: [], bottomRow: [] }
    }

    const top: GalleryImage[] = []
    const bottom: GalleryImage[] = []

    images.forEach((img, index) => {
      if (index % 2 === 0) {
        top.push(img)
      } else {
        bottom.push(img)
      }
    })

    return { topRow: top, bottomRow: bottom }
  }, [images])

  // Handle empty images array - early return AFTER all hooks
  if (!images || images.length === 0) {
    return (
      <div
        ref={focusWrapperRef}
        className={clsx(styles.focusWrapper, className)}
        role="region"
        aria-label="Image gallery"
        tabIndex={0}
      >
        <div ref={containerRef} className={styles.container}>
          <div ref={trackRef} className={styles.track}>
            <p className={styles.emptyMessage}>No images to display</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={focusWrapperRef}
      className={clsx(styles.focusWrapper, className)}
      role="region"
      aria-label="Image gallery. Use Tab to browse images."
      tabIndex={0}
      onFocus={handleGalleryFocus}
      onBlur={handleGalleryBlur}
      onKeyDown={handleGalleryKeyDown}
    >
      <div
        ref={containerRef}
        className={styles.container}
        onClick={handleContainerClick}
      >
        <div ref={trackRef} className={styles.track}>
          <GalleryRow
            images={topRow}
            rowRef={topRowRef}
            rowPosition="top"
            getHref={resolvedGetHref}
          />
          <GalleryRow
            images={bottomRow}
            rowRef={bottomRowRef}
            rowPosition="bottom"
            getHref={resolvedGetHref}
          />
        </div>
      </div>
    </div>
  )
})
