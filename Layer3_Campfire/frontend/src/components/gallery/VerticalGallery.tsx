'use client'

import { useMemo, useRef, memo, useCallback, useState, useLayoutEffect } from 'react'
import Image from 'next/image'
import { InlineIcon } from '@/components/ui'
import { useVerticalGallery } from '@/hooks/useVerticalGallery'
import { GALLERY } from '@/lib/constants'
import styles from './VerticalGallery.module.css'
import clsx from 'clsx'

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Calculates the number of duplicate sets needed for seamless infinite scroll.
 * Formula: ceil(containerHeight / estimatedSetHeight) + 1
 * 
 * @param columnImageCount - Number of images in one column (before duplication)
 * @param containerHeight - Height of the container viewport
 * @param estimatedImageHeight - Conservative estimate of average image height (default: 120px)
 * @returns Number of sets needed (minimum 2)
 */
const calculateSetsNeeded = (
  columnImageCount: number,
  containerHeight: number,
  estimatedImageHeight: number = GALLERY.ESTIMATED_IMAGE_HEIGHT
): number => {
  if (columnImageCount === 0) return GALLERY.DEFAULT_DUPLICATE_SETS
  
  // Estimate set height based on average image height + gaps
  const gapSize = 16 // matches --space-md
  const estimatedSetHeight = (columnImageCount * estimatedImageHeight) + (columnImageCount * gapSize)
  
  // Need enough sets so content extends beyond viewport at wrap point
  const setsNeeded = Math.ceil(containerHeight / estimatedSetHeight) + 1
  
  // Minimum 2 sets, maximum 5 (to prevent excessive DOM nodes)
  return Math.max(2, Math.min(5, setsNeeded))
}

/**
 * Creates an array with the source array repeated n times
 */
const repeatArray = <T,>(arr: T[], times: number): T[] => {
  const result: T[] = []
  for (let i = 0; i < times; i++) {
    result.push(...arr)
  }
  return result
}

// ============================================================================
// Types
// ============================================================================

export interface GalleryImage {
  id: string
  src: string
  alt: string
  caption?: string
}

interface VerticalGalleryProps {
  images: GalleryImage[]
  scrollSpeed?: number
  className?: string
  columns?: 1 | 2
  autoScroll?: boolean
  showActions?: boolean
  onImageAction?: (image: GalleryImage) => void
}

interface GalleryColumnProps {
  images: GalleryImage[]
  columnRef: React.RefObject<HTMLDivElement | null>
  columnSide: 'left' | 'right'
  selectedImageId: string | null
  onImageHover: (imageId: string) => void
  onImageLeave: (imageId: string) => void
  showActions?: boolean
  onImageAction?: (image: GalleryImage) => void
  /** Number of images from the top to eagerly load (rest are lazy) */
  priorityCount?: number
}

// ============================================================================
// Sub-Components
// ============================================================================

/**
 * GalleryColumn - Renders a single column of duplicated images
 * Memoized to prevent unnecessary re-renders when props are stable
 * 
 * Note: Individual images have tabIndex={-1} because keyboard navigation
 * is handled at the container level via programmatic focus management.
 */
const GalleryColumn = memo(function GalleryColumn({
  images,
  columnRef,
  columnSide,
  selectedImageId,
  onImageHover,
  onImageLeave,
  showActions,
  onImageAction,
  priorityCount = 3,
}: GalleryColumnProps) {
  return (
    <div ref={columnRef} className={styles.column} data-column={columnSide}>
      {images.map((image, index) => {
        const instanceId = `${columnSide}-${image.id}-${index}`
        const isSelected = selectedImageId === instanceId
        const isDimmed = selectedImageId !== null && !isSelected

        return (
          <div
            key={instanceId}
            className={clsx(
              styles.imageWrapper,
              isSelected && styles.selected,
              isDimmed && styles.dimmed
            )}
            data-gallery-image
            data-image-id={image.id}
            data-instance-id={instanceId}
            onMouseEnter={() => onImageHover(instanceId)}
            onMouseLeave={() => onImageLeave(instanceId)}
            onClick={() => onImageAction?.(image)}
            tabIndex={-1}
            aria-label={`View ${image.alt}`}
          >
            <div className={styles.imageContainer}>
              {/*
                Only the first few images (above the fold) use priority for
                eager loading. The rest use lazy loading to avoid blocking
                the main thread with 46 concurrent server-side optimizations.
                AVIF/WebP conversion + responsive sizes keep transfer small.
              */}
              <Image
                src={image.src}
                alt={image.alt}
                width={2500}
                height={1600}
                priority={index < priorityCount}
                loading={index < priorityCount ? 'eager' : 'lazy'}
                sizes="(max-width: 768px) 100vw, 50vw"
                className={styles.image}
                draggable={false}
                style={{ width: '100%', height: 'auto', objectFit: 'cover' }}
              />
            </div>
            <div className={styles.caption}>
              <span className={styles.captionText}>
                {image.caption || image.alt}
              </span>
              <button
                className={styles.captionButton}
                aria-label={`View details for ${image.alt}`}
                tabIndex={-1}
                onClick={(e) => {
                  e.stopPropagation()
                  onImageAction?.(image)
                }}
              >
                <InlineIcon name="arrow-right" size={16} />
              </button>
            </div>
            {showActions && (
              <div className={styles.actionButtons}>
                <button
                  className={styles.actionButton}
                  onClick={(e) => {
                    e.stopPropagation()
                    onImageAction?.(image)
                  }}
                >
                  View Details
                </button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
})

// ============================================================================
// Main Component
// ============================================================================

/**
 * VerticalGallery - Infinite scrolling image gallery for desktop
 * Uses Web Animations API for smooth, performant scrolling.
 *
 * @param props - Gallery configuration props
 * @param props.images - Array of images to display
 * @param props.scrollSpeed - Pixels per second for auto-scroll (default: 30)
 * @param props.className - Optional additional CSS classes
 */
export const VerticalGallery = memo(function VerticalGallery({
  images,
  scrollSpeed = 30,
  className,
  columns = 2,
  autoScroll = true,
  showActions = false,
  onImageAction,
}: VerticalGalleryProps) {
  const leftColumnRef = useRef<HTMLDivElement>(null)
  const rightColumnRef = useRef<HTMLDivElement>(null)
  const containerHeightRef = useRef<HTMLDivElement>(null)

  // Track container height as ref (no rerenders on measurement changes)
  const heightValueRef = useRef<number>(
    typeof window !== 'undefined' ? window.innerHeight : GALLERY.SSR_VIEWPORT_HEIGHT
  )

  // Split images into two columns for staggered effect
  // Moved BEFORE early return to comply with React hooks rules
  const { leftColumn, rightColumn } = useMemo(() => {
    if (!images || images.length === 0) {
      return { leftColumn: [], rightColumn: [] }
    }

    const left: GalleryImage[] = []
    const right: GalleryImage[] = []

    images.forEach((img, index) => {
      if (index % 2 === 0) {
        left.push(img)
      } else {
        right.push(img)
      }
    })

    return { leftColumn: left, rightColumn: right }
  }, [images])

  // setsNeeded is state - only triggers rerender when value actually changes
  // Uses monotonic increase pattern to prevent oscillation during layout settling
  const [setsNeeded, setSetsNeeded] = useState<number>(GALLERY.DEFAULT_DUPLICATE_SETS)

  // Measure container height and calculate setsNeeded
  // Only updates state when setsNeeded needs to INCREASE (prevents oscillation)
  useLayoutEffect(() => {
    const container = containerHeightRef.current
    if (!container) return

    const updateSetsNeeded = () => {
      const rect = container.getBoundingClientRect()
      if (rect.height > 0) {
        heightValueRef.current = rect.height
        const minColumnSize = Math.min(leftColumn.length, rightColumn.length)
        const newSetsNeeded = calculateSetsNeeded(minColumnSize, rect.height)
        // Only INCREASE setsNeeded, never decrease (prevents oscillation during layout settling)
        // Adding more duplicate sets changes layout, which can trigger new measurements
        // By only allowing increases, we stabilize after the first correct calculation
        setSetsNeeded(prev => newSetsNeeded > prev ? newSetsNeeded : prev)
      }
    }

    updateSetsNeeded()

    const resizeObserver = new ResizeObserver(updateSetsNeeded)
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
    }
  }, [leftColumn.length, rightColumn.length])

  // Initialize the gallery hook with dynamic duplicate sets
  const {
    containerRef,
    trackRef,
    selectedImageId,
    handleImageHover,
    handleImageLeave,
    pauseForKeyboard,
    resumeFromKeyboard,
    isKeyboardMode,
  } = useVerticalGallery({
    scrollSpeed,
    enabled: autoScroll,
    leftColumnRef,
    rightColumnRef,
    duplicateSets: autoScroll ? setsNeeded : 1,
  })

  // Track focused image index for keyboard navigation (null = container focused, not an image)
  const [focusedImageIndex, setFocusedImageIndex] = useState<number | null>(null)
  const visibleImagesRef = useRef<HTMLElement[]>([])

  // ============================================================================
  // Keyboard Navigation Helpers
  // ============================================================================

  /**
   * Calculates which images are fully visible and returns them sorted by visual position.
   * Sort order: column 1 (left) top-to-bottom, then column 2 (right) top-to-bottom.
   */
  const calculateVisibleImages = useCallback((): HTMLElement[] => {
    const container = containerRef.current
    if (!container) return []

    const allImages = container.querySelectorAll('[data-gallery-image]')
    const containerRect = container.getBoundingClientRect()

    // Effective visible area: intersection of container bounds and viewport.
    // The container may extend beyond the viewport, so clamp to screen edges.
    const effectiveTop = Math.max(containerRect.top, 0)
    const effectiveBottom = Math.min(containerRect.bottom, window.innerHeight)

    // Filter to only fully visible images (within both container and viewport)
    const visible = Array.from(allImages).filter((img): img is HTMLElement => {
      if (!(img instanceof HTMLElement)) return false
      const rect = img.getBoundingClientRect()
      return rect.top >= effectiveTop && rect.bottom <= effectiveBottom
    })

    // Sort by visual position: column first (left x position), then top position within column
    visible.sort((a, b) => {
      const rectA = a.getBoundingClientRect()
      const rectB = b.getBoundingClientRect()
      // Use 50px threshold to group elements in the same column
      if (Math.abs(rectA.left - rectB.left) > 50) {
        return rectA.left - rectB.left // Different columns - sort by x position
      }
      return rectA.top - rectB.top // Same column - sort by y position
    })

    // Deduplicate by image ID — infinite scroll duplicates images across sets,
    // so multiple copies of the same image can be in the viewport simultaneously.
    // Keep only the first (topmost) element per unique image ID.
    const seen = new Set<string>()
    const deduped = visible.filter((el) => {
      const id = el.getAttribute('data-image-id')
      if (!id || seen.has(id)) return false
      seen.add(id)
      return true
    })

    return deduped
  }, [containerRef])

  /**
   * Handle focus entering the gallery container
   */
  const handleGalleryFocus = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    // Only trigger if focus came from outside the gallery
    const container = containerRef.current
    if (!container) return
    
    const relatedTarget = e.relatedTarget as Node | null
    const focusCameFromInside = relatedTarget && container.contains(relatedTarget)
    
    if (!focusCameFromInside) {
      // Focus entered from outside - enter keyboard mode and pause animation
      pauseForKeyboard()
      // Calculate visible images now that animation is paused
      visibleImagesRef.current = calculateVisibleImages()
      // Reset focused index - user needs to Tab again to focus first image
      setFocusedImageIndex(null)
    }
  }, [containerRef, pauseForKeyboard, calculateVisibleImages])

  /**
   * Handle focus leaving the gallery container
   */
  const handleGalleryBlur = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    // Only trigger if focus left the gallery entirely
    const container = containerRef.current
    if (!container) return
    
    const relatedTarget = e.relatedTarget as Node | null
    const focusStayedInside = relatedTarget && container.contains(relatedTarget)
    
    if (!focusStayedInside) {
      // Focus left the gallery - clear selection, exit keyboard mode, and resume animation
      handleImageLeave()
      resumeFromKeyboard()
      setFocusedImageIndex(null)
      visibleImagesRef.current = []
    }
  }, [containerRef, handleImageLeave, resumeFromKeyboard])

  /**
   * Handle keyboard navigation within the gallery
   */
  const handleGalleryKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    // Handle Escape - exit keyboard mode and return focus to container
    if (e.key === 'Escape' && isKeyboardMode) {
      e.preventDefault()
      setFocusedImageIndex(null)
      containerRef.current?.focus()
      return
    }

    // Handle Enter/Space - activate currently focused image action
    if ((e.key === 'Enter' || e.key === ' ') && isKeyboardMode) {
      if (focusedImageIndex !== null) {
        e.preventDefault()
        const focusedElement = visibleImagesRef.current[focusedImageIndex]
        const imageId = focusedElement?.getAttribute('data-image-id')
        if (imageId) {
          const image = images.find((img) => img.id === imageId)
          if (image) {
            onImageAction?.(image)
          }
        }
      }
      return
    }

    // Only handle Tab key in keyboard mode
    if (e.key !== 'Tab' || !isKeyboardMode) return

    const visibleImages = visibleImagesRef.current
    if (visibleImages.length === 0) return

    const currentIndex = focusedImageIndex

    if (e.shiftKey) {
      // SHIFT+TAB: move backwards
      if (currentIndex === null) {
        // On container, going backwards - clear selection, exit keyboard mode, let browser handle exit
        handleImageLeave()
        resumeFromKeyboard()
        return
      }
      if (currentIndex === 0) {
        // At first image - go back to container
        e.preventDefault()
        handleImageLeave()
        setFocusedImageIndex(null)
        containerRef.current?.focus()
      } else {
        // Move to previous image
        e.preventDefault()
        const newIndex = currentIndex - 1
        setFocusedImageIndex(newIndex)
        visibleImages[newIndex]?.focus()
        // Trigger hover effect on the newly focused image
        const instanceId = visibleImages[newIndex]?.getAttribute('data-instance-id')
        if (instanceId) handleImageHover(instanceId)
      }
    } else {
      // TAB: move forward
      if (currentIndex === null) {
        // On container - move to first visible image
        e.preventDefault()
        setFocusedImageIndex(0)
        visibleImages[0]?.focus()
        // Trigger hover effect on the newly focused image
        const instanceId = visibleImages[0]?.getAttribute('data-instance-id')
        if (instanceId) handleImageHover(instanceId)
      } else if (currentIndex >= visibleImages.length - 1) {
        // At last image - exit gallery forwards
        // Clear selection, exit keyboard mode, let browser handle Tab
        handleImageLeave()
        setFocusedImageIndex(null)
        resumeFromKeyboard()
        return
      } else {
        // Move to next image
        e.preventDefault()
        const newIndex = currentIndex + 1
        setFocusedImageIndex(newIndex)
        visibleImages[newIndex]?.focus()
        // Trigger hover effect on the newly focused image
        const instanceId = visibleImages[newIndex]?.getAttribute('data-instance-id')
        if (instanceId) handleImageHover(instanceId)
      }
    }
  }, [isKeyboardMode, focusedImageIndex, containerRef, resumeFromKeyboard, handleImageHover, handleImageLeave, images, onImageAction])

  // Duplicate images for seamless infinite loop
  // Number of sets is dynamically calculated to ensure content fills visible area
  const effectiveSets = autoScroll ? setsNeeded : 1
  const duplicatedLeft = useMemo(
    () => repeatArray(leftColumn, effectiveSets),
    [leftColumn, effectiveSets]
  )
  const duplicatedRight = useMemo(
    () => repeatArray(rightColumn, effectiveSets),
    [rightColumn, effectiveSets]
  )
  // For single column mode, combine all images into one array
  const singleColumnImages = useMemo(
    () => (columns === 1 ? repeatArray(images, effectiveSets) : []),
    [columns, images, effectiveSets]
  )

  // Merge refs for container (hook's containerRef + our height measurement ref)
  const setContainerRefs = useCallback((node: HTMLDivElement | null) => {
    // Assign to hook's containerRef
    if (containerRef && 'current' in containerRef) {
      (containerRef as React.MutableRefObject<HTMLDivElement | null>).current = node
    }
    // Assign to our height measurement ref
    (containerHeightRef as React.MutableRefObject<HTMLDivElement | null>).current = node
  }, [containerRef])

  // Handle empty images array - early return AFTER all hooks
  if (!images || images.length === 0) {
    return (
      <div
        ref={setContainerRefs}
        className={clsx(styles.container, className)}
        role="region"
        aria-label="Image gallery"
        tabIndex={0}
      >
        <div ref={trackRef} className={styles.track}>
          <p className={styles.emptyMessage}>No images to display</p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={setContainerRefs}
      className={clsx(styles.container, columns === 1 && styles.singleColumn, className)}
      role="region"
      aria-label="Image gallery. Use Tab to browse images."
      tabIndex={0}
      onFocus={handleGalleryFocus}
      onBlur={handleGalleryBlur}
      onKeyDown={handleGalleryKeyDown}
    >
      <div ref={trackRef} className={clsx(styles.track, columns === 1 && styles.trackSingleColumn)}>
        {columns === 1 ? (
          <GalleryColumn
            images={singleColumnImages}
            columnRef={leftColumnRef}
            columnSide="left"
            selectedImageId={selectedImageId}
            onImageHover={handleImageHover}
            onImageLeave={handleImageLeave}
            showActions={showActions}
            onImageAction={onImageAction}
          />
        ) : (
          <>
            <GalleryColumn
              images={duplicatedLeft}
              columnRef={leftColumnRef}
              columnSide="left"
              selectedImageId={selectedImageId}
              onImageHover={handleImageHover}
              onImageLeave={handleImageLeave}
              showActions={showActions}
              onImageAction={onImageAction}
              priorityCount={3}
            />
            <GalleryColumn
              images={duplicatedRight}
              columnRef={rightColumnRef}
              columnSide="right"
              selectedImageId={selectedImageId}
              onImageHover={handleImageHover}
              onImageLeave={handleImageLeave}
              showActions={showActions}
              onImageAction={onImageAction}
              priorityCount={3}
            />
          </>
        )}
      </div>
    </div>
  )
})
