'use client'

import { useRef, useState, useCallback, useEffect, useLayoutEffect } from 'react'
import { measureSingleSetWidth, wrapTime } from '@/lib/gallery-utils'

// ============================================================================
// Constants
// ============================================================================

/** Minimum pixels of movement before drag is recognized */
const DRAG_THRESHOLD_PX = 5

/** Debounce delay for resize observer recalculations (ms) */
const RESIZE_DEBOUNCE_MS = 100

/** Fraction of accumulated delta to apply per frame (0.15 = 15% per frame) */
const WHEEL_SMOOTHING_FACTOR = 0.15

/** Stop rAF loop when accumulated delta falls below this threshold (pixels) */
const WHEEL_DELTA_THRESHOLD = 0.5

// ============================================================================
// Types
// ============================================================================

interface UseHorizontalGalleryOptions {
  /** Pixels per second for auto-scroll speed */
  scrollSpeed?: number
  /** Whether the animation is enabled */
  enabled?: boolean
  /** Optional ref for top row element */
  topRowRef?: React.RefObject<HTMLDivElement | null>
  /** Optional ref for bottom row element */
  bottomRowRef?: React.RefObject<HTMLDivElement | null>
  /** Callback when a selected image is clicked a second time */
  onSecondClick?: (imageId: string) => void
}

interface UseHorizontalGalleryReturn {
  /** Ref to attach to the container element */
  containerRef: React.RefObject<HTMLDivElement | null>
  /** Ref to attach to the track element */
  trackRef: React.RefObject<HTMLDivElement | null>
  /** Currently selected/clicked image ID */
  selectedImageId: string | null
  /** Handler for image click/selection */
  handleImageClick: (imageId: string) => void
  /** Handler for container click (event delegation) */
  handleContainerClick: (e: React.MouseEvent) => void
  /** Pause animation for keyboard navigation mode */
  pauseForKeyboard: () => void
  /** Resume animation when exiting keyboard navigation mode */
  resumeFromKeyboard: () => void
  /** Whether keyboard navigation mode is active */
  isKeyboardMode: boolean
  /** Apply selection state to DOM elements (for keyboard navigation) */
  applySelectionState: (selectedElement: HTMLElement | null) => void
  /** Clear selection and resume animations */
  clearSelection: () => void
}

interface RowAnimationState {
  animation: Animation
  duration: number // in milliseconds
  setWidth: number // width of one set of images
}

// ============================================================================
// Helper Hooks
// ============================================================================

/**
 * Hook to wait for all images in a container to load before returning true.
 * Uses useLayoutEffect to ensure measurements can proceed immediately after load.
 *
 * @param trackRef - Ref to container element containing img elements
 * @returns boolean indicating if all images have loaded (or errored)
 */
const useImagesLoaded = (trackRef: React.RefObject<HTMLDivElement | null>): boolean => {
  const [imagesLoaded, setImagesLoaded] = useState(false)

  useLayoutEffect(() => {
    if (!trackRef.current) {
      setImagesLoaded(true)
      return
    }

    const images = trackRef.current.querySelectorAll('img')
    let loadedCount = 0
    const totalImages = images.length

    if (totalImages === 0) {
      setImagesLoaded(true)
      return
    }

    const checkAllLoaded = () => {
      loadedCount++
      if (loadedCount >= totalImages) {
        setImagesLoaded(true)
      }
    }

    images.forEach((img) => {
      if (img.complete && img.naturalHeight > 0) {
        checkAllLoaded()
      } else {
        img.addEventListener('load', checkAllLoaded, { once: true })
        img.addEventListener('error', checkAllLoaded, { once: true })
      }
    })

    return () => {
      images.forEach((img) => {
        img.removeEventListener('load', checkAllLoaded)
        img.removeEventListener('error', checkAllLoaded)
      })
    }
  }, [trackRef])

  return imagesLoaded
}

// ============================================================================
// Main Hook
// ============================================================================

/**
 * Hook for horizontal infinite scrolling gallery with Web Animations API.
 *
 * Features:
 * - Auto-scroll with seamless infinite loop per row
 * - Click-to-select with pause
 * - Drag-to-scroll via pointer events
 * - Wheel scroll support (horizontal and vertical delta)
 * - Keyboard navigation (Enter/Space/Escape)
 * - Resume on pointer leave
 * - ResizeObserver for dynamic width changes
 * - Respects prefers-reduced-motion
 *
 * @param options - Configuration options
 * @returns Object containing refs and handlers for the gallery
 */
export const useHorizontalGallery = ({
  scrollSpeed = 30,
  enabled = true,
  topRowRef,
  bottomRowRef,
  onSecondClick,
}: UseHorizontalGalleryOptions = {}): UseHorizontalGalleryReturn => {
  const containerRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)

  // Animation state for each row
  const topAnimStateRef = useRef<RowAnimationState | null>(null)
  const bottomAnimStateRef = useRef<RowAnimationState | null>(null)

  // Mutable state refs (used in event handlers to avoid stale closures)
  const isPausedRef = useRef(false)
  const selectedImageIdRef = useRef<string | null>(null)
  const isInitializedRef = useRef(false)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const resizeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const topRowElementRef = useRef<HTMLElement | null>(null)
  const bottomRowElementRef = useRef<HTMLElement | null>(null)
  const selectedElementRef = useRef<HTMLElement | null>(null)

  // Wheel scroll smoothing state
  const accumulatedDeltaRef = useRef<number>(0)
  const wheelRafIdRef = useRef<number | null>(null)
  const isWheelScrollingRef = useRef<boolean>(false)

  // React state for UI updates
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null)
  const [isKeyboardMode, setIsKeyboardMode] = useState(false)
  const isKeyboardModeRef = useRef(false)

  // Use extracted hooks
  const imagesLoaded = useImagesLoaded(trackRef)

  // Sync keyboard mode to ref for use in event handler callbacks
  useLayoutEffect(() => {
    isKeyboardModeRef.current = isKeyboardMode
  }, [isKeyboardMode])

  // Sync selectedImageId to ref for use in event handler callbacks
  useLayoutEffect(() => {
    selectedImageIdRef.current = selectedImageId
  }, [selectedImageId])

  // ============================================================================
  // Selection State Management
  // ============================================================================

  /**
   * Apply selection state to ALL gallery images via data attributes.
   * This allows CSS to handle the visual states without React re-renders.
   */
  const applySelectionState = useCallback((selectedElement: HTMLElement | null) => {
    if (!trackRef.current) return

    const allImages = trackRef.current.querySelectorAll('[data-gallery-image]')

    allImages.forEach((el) => {
      if (selectedElement === null) {
        el.removeAttribute('data-selected')
        el.removeAttribute('data-dimmed')
      } else if (el === selectedElement) {
        el.setAttribute('data-selected', 'true')
        el.removeAttribute('data-dimmed')
      } else {
        el.removeAttribute('data-selected')
        el.setAttribute('data-dimmed', 'true')
      }
    })

    selectedElementRef.current = selectedElement
  }, [])

  // ============================================================================
  // Animation Control Functions
  // ============================================================================

  const pauseAnimations = useCallback(() => {
    isPausedRef.current = true
    topAnimStateRef.current?.animation.pause()
    bottomAnimStateRef.current?.animation.pause()
  }, [])

  const resumeAnimations = useCallback(() => {
    isPausedRef.current = false
    topAnimStateRef.current?.animation.play()
    bottomAnimStateRef.current?.animation.play()
  }, [])

  // ============================================================================
  // Wheel Scroll Smoothing
  // ============================================================================

  /**
   * Cancels the wheel scroll rAF loop and resets state.
   * Call this when wheel scrolling should be interrupted.
   */
  const cancelWheelScroll = useCallback(() => {
    if (wheelRafIdRef.current !== null) {
      cancelAnimationFrame(wheelRafIdRef.current)
      wheelRafIdRef.current = null
    }
    accumulatedDeltaRef.current = 0
    isWheelScrollingRef.current = false
  }, [])

  /**
   * Smoothly applies accumulated wheel delta across multiple frames.
   * Called via requestAnimationFrame for consistent 60fps updates.
   * Handles both top and bottom row animations.
   */
  const applyWheelDelta = useCallback(() => {
    // Guard: ensure animations are initialized
    if (!topAnimStateRef.current || !bottomAnimStateRef.current) {
      wheelRafIdRef.current = null
      return
    }

    const delta = accumulatedDeltaRef.current
    
    // Stop loop if delta is below threshold
    if (Math.abs(delta) < WHEEL_DELTA_THRESHOLD) {
      accumulatedDeltaRef.current = 0
      wheelRafIdRef.current = null
      isWheelScrollingRef.current = false
      
      // Resume auto-scroll if no image is selected and not in keyboard mode
      // Note: resumeAnimations() in horizontal gallery already sets isPausedRef = false
      if (!selectedImageIdRef.current && !isKeyboardModeRef.current) {
        resumeAnimations()
      }
      return
    }

    // Calculate portion to apply this frame (15% of remaining)
    const deltaToApply = delta * WHEEL_SMOOTHING_FACTOR
    
    // Convert pixels to animation time change
    const topState = topAnimStateRef.current
    const bottomState = bottomAnimStateRef.current
    
    const topTimeChange = (deltaToApply / topState.setWidth) * topState.duration
    const bottomTimeChange = (deltaToApply / bottomState.setWidth) * bottomState.duration

    // Apply to animations
    const topCurrentTime = topState.animation.currentTime
    const bottomCurrentTime = bottomState.animation.currentTime

    if (typeof topCurrentTime === 'number') {
      topState.animation.currentTime = wrapTime(topCurrentTime + topTimeChange, topState.duration)
    }
    if (typeof bottomCurrentTime === 'number') {
      bottomState.animation.currentTime = wrapTime(bottomCurrentTime + bottomTimeChange, bottomState.duration)
    }

    // Subtract applied amount from accumulator
    accumulatedDeltaRef.current -= deltaToApply

    // Schedule next frame
    wheelRafIdRef.current = requestAnimationFrame(applyWheelDelta)
  }, [resumeAnimations])

  // Pause animation for keyboard navigation mode
  const pauseForKeyboard = useCallback(() => {
    cancelWheelScroll()
    setIsKeyboardMode(true)
    isKeyboardModeRef.current = true
    // Note: pauseAnimations() already sets isPausedRef = true
    pauseAnimations()
  }, [pauseAnimations, cancelWheelScroll])

  // Resume animation when exiting keyboard navigation mode
  const resumeFromKeyboard = useCallback(() => {
    setIsKeyboardMode(false)
    isKeyboardModeRef.current = false
    // Only resume if no image is selected (e.g., via click)
    if (!selectedImageIdRef.current) {
      isPausedRef.current = false
      resumeAnimations()
    }
  }, [resumeAnimations])

  // Clear selection and resume scrolling
  const clearSelection = useCallback(() => {
    if (!selectedImageIdRef.current && !selectedElementRef.current) return
    selectedImageIdRef.current = null
    setSelectedImageId(null)
    applySelectionState(null)
    resumeAnimations()
  }, [applySelectionState, resumeAnimations])

  // Handle image click - toggle selection
  const handleImageClick = useCallback((imageId: string) => {
    if (selectedImageIdRef.current === imageId) {
      clearSelection()
      return
    }

    cancelWheelScroll() // Interrupt wheel scroll
    selectedImageIdRef.current = imageId
    setSelectedImageId(imageId)
    pauseAnimations()
  }, [clearSelection, pauseAnimations, cancelWheelScroll])

  // Handle container click - use event delegation
  const handleContainerClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement
    const galleryImage = target.closest('[data-gallery-image]') as HTMLElement | null

    if (galleryImage) {
      const imageId = galleryImage.getAttribute('data-image-id')
      if (imageId) {
        if (selectedElementRef.current === galleryImage) {
          // Second click on selected image — navigate
          onSecondClick?.(imageId)
        } else {
          cancelWheelScroll() // Interrupt wheel scroll
          selectedImageIdRef.current = imageId
          setSelectedImageId(imageId)
          applySelectionState(galleryImage)
          pauseAnimations()
        }
      }
    } else if (selectedImageIdRef.current || selectedElementRef.current) {
      clearSelection()
    }
  }, [clearSelection, applySelectionState, pauseAnimations, cancelWheelScroll, onSecondClick])

  // ============================================================================
  // Animation Creation
  // ============================================================================

  /**
   * Creates or updates animations for both rows.
   * Preserves scroll position during resize by scaling currentTime.
   */
  const setupAnimations = useCallback((topRow: HTMLElement, bottomRow: HTMLElement): boolean => {
    // Images are triplicated (3x) in React for fast scroll buffer
    const topOriginalCount = Math.floor(topRow.children.length / 3)
    const bottomOriginalCount = Math.floor(bottomRow.children.length / 3)

    if (topOriginalCount === 0 || bottomOriginalCount === 0) return false

    // Calculate width of ONE set of images
    const topSetWidth = measureSingleSetWidth(topRow, topOriginalCount)
    const bottomSetWidth = measureSingleSetWidth(bottomRow, bottomOriginalCount)

    if (topSetWidth <= 0 || bottomSetWidth <= 0) return false

    // Calculate durations (in milliseconds)
    const topDuration = (topSetWidth / scrollSpeed) * 1000
    const bottomDuration = (bottomSetWidth / scrollSpeed) * 1000

    // Get current time positions if animations exist (for resize preservation)
    const existingTopTime = topAnimStateRef.current?.animation.currentTime
    const existingBottomTime = bottomAnimStateRef.current?.animation.currentTime

    // Cancel existing animations
    topAnimStateRef.current?.animation.cancel()
    bottomAnimStateRef.current?.animation.cancel()

    // Create new animations
    const topAnimation = topRow.animate(
      [
        { transform: 'translate3d(0, 0, 0)' },
        { transform: `translate3d(-${topSetWidth}px, 0, 0)` }
      ],
      {
        duration: topDuration,
        iterations: Infinity,
        easing: 'linear'
      }
    )

    const bottomAnimation = bottomRow.animate(
      [
        { transform: 'translate3d(0, 0, 0)' },
        { transform: `translate3d(-${bottomSetWidth}px, 0, 0)` }
      ],
      {
        duration: bottomDuration,
        iterations: Infinity,
        easing: 'linear'
      }
    )

    // Restore time position if we had one (for resize)
    if (typeof existingTopTime === 'number' && topAnimStateRef.current) {
      const progress = existingTopTime / topAnimStateRef.current.duration
      topAnimation.currentTime = progress * topDuration
    }
    if (typeof existingBottomTime === 'number' && bottomAnimStateRef.current) {
      const progress = existingBottomTime / bottomAnimStateRef.current.duration
      bottomAnimation.currentTime = progress * bottomDuration
    }

    // Store animation state
    topAnimStateRef.current = {
      animation: topAnimation,
      duration: topDuration,
      setWidth: topSetWidth,
    }
    bottomAnimStateRef.current = {
      animation: bottomAnimation,
      duration: bottomDuration,
      setWidth: bottomSetWidth,
    }

    // Apply current pause state
    if (isPausedRef.current) {
      topAnimation.pause()
      bottomAnimation.pause()
    }

    return true
  }, [scrollSpeed])

  // ============================================================================
  // Keyboard Handler Effect
  // ============================================================================

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedImageIdRef.current) {
        clearSelection()
        return
      }

      if (e.key === 'Enter' || e.key === ' ') {
        const target = e.target as HTMLElement
        const galleryImage = target.closest('[data-gallery-image]') as HTMLElement | null

        if (galleryImage && containerRef.current?.contains(galleryImage)) {
          e.preventDefault()
          const imageId = galleryImage.getAttribute('data-image-id')
          if (imageId) {
            if (selectedElementRef.current === galleryImage) {
              clearSelection()
            } else {
              cancelWheelScroll() // Interrupt wheel scroll
              selectedImageIdRef.current = imageId
              setSelectedImageId(imageId)
              applySelectionState(galleryImage)
              pauseAnimations()
            }
          }
        }
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [clearSelection, applySelectionState, pauseAnimations, cancelWheelScroll])

  // ============================================================================
  // Document Click Handler Effect (Deselect on Outside Click)
  // ============================================================================

  useEffect(() => {
    const handleDocumentClick = (e: MouseEvent) => {
      if (!selectedImageIdRef.current && !selectedElementRef.current) return

      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        clearSelection()
      }
    }

    document.addEventListener('click', handleDocumentClick)
    return () => document.removeEventListener('click', handleDocumentClick)
  }, [clearSelection])

  // ============================================================================
  // Animation Initialization Effect
  // ============================================================================

  useLayoutEffect(() => {
    if (!containerRef.current || !trackRef.current || !enabled || !imagesLoaded) return
    if (isInitializedRef.current) return

    const topRow = topRowRef?.current || trackRef.current.querySelector('[data-row="top"]')
    const bottomRow = bottomRowRef?.current || trackRef.current.querySelector('[data-row="bottom"]')

    if (!topRow || !bottomRow || !(topRow instanceof HTMLElement) || !(bottomRow instanceof HTMLElement)) {
      return
    }

    topRowElementRef.current = topRow
    bottomRowElementRef.current = bottomRow
    isInitializedRef.current = true

    if (!setupAnimations(topRow, bottomRow)) {
      isInitializedRef.current = false
      return
    }

    // Setup ResizeObserver with proper debounce
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserverRef.current = new ResizeObserver(() => {
        if (resizeTimeoutRef.current) {
          clearTimeout(resizeTimeoutRef.current)
        }

        resizeTimeoutRef.current = setTimeout(() => {
          if (topRowElementRef.current && bottomRowElementRef.current && isInitializedRef.current) {
            setupAnimations(topRowElementRef.current, bottomRowElementRef.current)
          }
        }, RESIZE_DEBOUNCE_MS)
      })

      resizeObserverRef.current.observe(topRow)
      resizeObserverRef.current.observe(bottomRow)
    }

    return () => {
      // Clear pending resize timeout to prevent memory leaks
      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current)
        resizeTimeoutRef.current = null
      }

      if (resizeObserverRef.current) {
        resizeObserverRef.current.disconnect()
        resizeObserverRef.current = null
      }

      topAnimStateRef.current?.animation.cancel()
      bottomAnimStateRef.current?.animation.cancel()
      topAnimStateRef.current = null
      bottomAnimStateRef.current = null
      isInitializedRef.current = false
      topRowElementRef.current = null
      bottomRowElementRef.current = null
    }
  }, [enabled, imagesLoaded, topRowRef, bottomRowRef, setupAnimations])

  // ============================================================================
  // Pointer/Drag Events Effect
  // ============================================================================

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container || !enabled || !imagesLoaded) return

    let isPointerDown = false
    let isDragging = false
    let dragStartX = 0
    let activePointerId: number | null = null

    const handlePointerDown = (e: PointerEvent) => {
      // Ignore clicks on buttons (e.g., caption button)
      if ((e.target as HTMLElement).closest('button')) return

      isPointerDown = true
      isDragging = false
      dragStartX = e.clientX
      activePointerId = e.pointerId
    }

    const handlePointerMove = (e: PointerEvent) => {
      if (!isPointerDown) return
      if (!topAnimStateRef.current || !bottomAnimStateRef.current) return

      const deltaX = e.clientX - dragStartX

      // Start dragging after threshold to distinguish from clicks
      if (!isDragging && Math.abs(deltaX) > DRAG_THRESHOLD_PX) {
        isDragging = true
        if (activePointerId !== null) {
          container.setPointerCapture(activePointerId)
        }
        cancelWheelScroll() // Interrupt wheel scroll
        pauseAnimations()
        // Clear any selection when drag starts - dragging is for exploration, not selection
        if (selectedImageIdRef.current || selectedElementRef.current) {
          selectedImageIdRef.current = null
          setSelectedImageId(null)
          applySelectionState(null)
        }
      }

      if (!isDragging) return

      // Convert pixel delta to time delta
      // Moving right (positive deltaX) = going back in time
      const topState = topAnimStateRef.current
      const bottomState = bottomAnimStateRef.current

      const topTimeChange = (-deltaX / topState.setWidth) * topState.duration
      const bottomTimeChange = (-deltaX / bottomState.setWidth) * bottomState.duration

      const topCurrentTime = topState.animation.currentTime
      const bottomCurrentTime = bottomState.animation.currentTime

      if (typeof topCurrentTime === 'number') {
        topState.animation.currentTime = wrapTime(topCurrentTime + topTimeChange, topState.duration)
      }
      if (typeof bottomCurrentTime === 'number') {
        bottomState.animation.currentTime = wrapTime(bottomCurrentTime + bottomTimeChange, bottomState.duration)
      }

      dragStartX = e.clientX
    }

    const handlePointerUp = (e: PointerEvent) => {
      if (!isPointerDown) return

      const wasDragging = isDragging
      isPointerDown = false
      isDragging = false
      activePointerId = null

      if (wasDragging && container.hasPointerCapture(e.pointerId)) {
        container.releasePointerCapture(e.pointerId)
      }

      // Resume auto-scroll after drag (selection was already cleared on drag start)
      if (wasDragging) {
        resumeAnimations()
      }

      // Prevent click event if we were dragging
      if (wasDragging) {
        const preventClick = (clickEvent: Event) => {
          clickEvent.stopPropagation()
          container.removeEventListener('click', preventClick, true)
        }
        container.addEventListener('click', preventClick, true)
      }
    }

    container.addEventListener('pointerdown', handlePointerDown)
    container.addEventListener('pointermove', handlePointerMove)
    container.addEventListener('pointerup', handlePointerUp)
    container.addEventListener('pointercancel', handlePointerUp)

    return () => {
      container.removeEventListener('pointerdown', handlePointerDown)
      container.removeEventListener('pointermove', handlePointerMove)
      container.removeEventListener('pointerup', handlePointerUp)
      container.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [enabled, imagesLoaded, pauseAnimations, resumeAnimations, applySelectionState, cancelWheelScroll])

  // ============================================================================
  // Pointer Leave Effect (Resume Animation)
  // ============================================================================

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container || !enabled || !imagesLoaded) return

    const handlePointerLeave = () => {
      // Don't resume if wheel scrolling is still applying delta
      // The rAF loop will resume auto-scroll when delta drains
      if (isWheelScrollingRef.current) return
      
      // Only resume if no image is selected AND not in keyboard navigation mode AND currently paused
      if (!selectedImageIdRef.current && !isKeyboardModeRef.current && isPausedRef.current) {
        resumeAnimations()
      }
    }

    container.addEventListener('pointerleave', handlePointerLeave)

    return () => {
      container.removeEventListener('pointerleave', handlePointerLeave)
    }
  }, [enabled, imagesLoaded, resumeAnimations])

  // ============================================================================
  // Return
  // ============================================================================

  return {
    containerRef,
    trackRef,
    selectedImageId,
    handleImageClick,
    handleContainerClick,
    pauseForKeyboard,
    resumeFromKeyboard,
    isKeyboardMode,
    applySelectionState,
    clearSelection,
  }
}

