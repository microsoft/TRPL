// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useRef, useState, useCallback, useEffect, useLayoutEffect } from 'react'
import { measureSingleSetHeight, wrapTime } from '@/lib/gallery-utils'
import { usePrefersReducedMotion } from './usePrefersReducedMotion'

// ============================================================================
// Constants
// ============================================================================

/** Minimum pixels of movement before drag is recognized */
const DRAG_THRESHOLD_PX = 5

/** Debounce delay for resize observer recalculations (ms) */
const RESIZE_DEBOUNCE_MS = 100

/** Debounce delay for hover events (ms) */
const HOVER_DEBOUNCE_MS = 250

/** Fraction of accumulated delta to apply per frame (0.15 = 15% per frame) */
const WHEEL_SMOOTHING_FACTOR = 0.15

/** Stop rAF loop when accumulated delta falls below this threshold (pixels) */
const WHEEL_DELTA_THRESHOLD = 0.5

// ============================================================================
// Types
// ============================================================================

interface UseVerticalGalleryOptions {
  /** Pixels per second for auto-scroll speed */
  scrollSpeed?: number
  /** Whether the animation is enabled */
  enabled?: boolean
  /** Optional ref for left column element */
  leftColumnRef?: React.RefObject<HTMLDivElement | null>
  /** Optional ref for right column element */
  rightColumnRef?: React.RefObject<HTMLDivElement | null>
  /** Number of duplicate sets in the columns (default: 2) */
  duplicateSets?: number
}

interface UseVerticalGalleryReturn {
  /** Ref to attach to the container element */
  containerRef: React.RefObject<HTMLDivElement | null>
  /** Ref to attach to the track element */
  trackRef: React.RefObject<HTMLDivElement | null>
  /** Currently selected/hovered image ID */
  selectedImageId: string | null
  /** Handler for image hover/selection */
  handleImageHover: (imageId: string) => void
  /** Handler for image leave/deselection */
  handleImageLeave: (imageId?: string) => void
  /** Pause animation for keyboard navigation mode */
  pauseForKeyboard: () => void
  /** Resume animation when exiting keyboard navigation mode */
  resumeFromKeyboard: () => void
  /** Whether keyboard navigation mode is active */
  isKeyboardMode: boolean
}

interface ColumnAnimationState {
  animation: Animation
  duration: number
  singleSetHeight: number
}

// ============================================================================
// Helper Hooks
// ============================================================================

/**
 * Hook to wait for all images in a container to load.
 * Uses a ref + callback pattern to avoid triggering re-renders when images load.
 *
 * This is necessary because:
 * - WAAPI animations need accurate height measurements
 * - getBoundingClientRect returns 0 for unloaded images
 * - We track both load and error events to avoid hanging on broken images
 *
 * @param trackRef - Ref to container element containing img elements
 * @param onLoadedCallback - Ref to callback function called when all images load
 * @returns Ref containing boolean indicating if all images have loaded
 */
const useImagesLoaded = (
  trackRef: React.RefObject<HTMLDivElement | null>,
  onLoadedCallback: React.RefObject<(() => void) | null>
): React.RefObject<boolean> => {
  const imagesLoadedRef = useRef(false)

  useLayoutEffect(() => {
    // trackRef.current is checked here, not as a dependency, because:
    // 1. The ref object itself is stable (same reference across renders)
    // 2. The .current value is set before useLayoutEffect runs
    // 3. We only need to run this once on mount
    if (!trackRef.current) {
      imagesLoadedRef.current = true
      return
    }

    // Only wait for eagerly-loaded gallery images, not icons or lazy images.
    // Lazy images won't load until scrolled into view, but the animation must
    // start first to scroll them — so waiting on them would deadlock.
    const allImages = trackRef.current.querySelectorAll('img')
    const images = Array.from(allImages).filter(img =>
      !img.src.includes('arrow-right') && img.loading !== 'lazy'
    )
    let loadedCount = 0
    const totalImages = images.length

    if (totalImages === 0) {
      imagesLoadedRef.current = true
      return
    }

    const checkAllLoaded = () => {
      loadedCount++
      if (loadedCount >= totalImages) {
        imagesLoadedRef.current = true
        // Call the callback to trigger animation setup
        onLoadedCallback.current?.()
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
  }, [trackRef, onLoadedCallback])

  return imagesLoadedRef
}

// ============================================================================
// Main Hook
// ============================================================================

/**
 * Hook for vertical infinite scrolling gallery with Web Animations API.
 *
 * Features:
 * - Auto-scroll with seamless infinite loop per column
 * - Hover-to-highlight with pause
 * - Drag-to-scroll via animation.currentTime manipulation
 * - Wheel scroll override
 * - Keyboard support (Enter/Space to select, Escape to deselect)
 * - ResizeObserver for dynamic height changes
 * - Respects prefers-reduced-motion
 *
 * @param options - Configuration options
 * @returns Object containing refs and handlers for the gallery
 */
export const useVerticalGallery = ({
  scrollSpeed = 30,
  enabled = true,
  leftColumnRef,
  rightColumnRef,
  duplicateSets = 2,
}: UseVerticalGalleryOptions = {}): UseVerticalGalleryReturn => {
  const containerRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)

  // Animation state for each column
  const leftAnimStateRef = useRef<ColumnAnimationState | null>(null)
  const rightAnimStateRef = useRef<ColumnAnimationState | null>(null)

  // Mutable state refs (used in event handlers to avoid stale closures)
  const isPausedRef = useRef(false)
  const selectedImageIdRef = useRef<string | null>(null)
  const isDraggingRef = useRef(false)
  const isInitializedRef = useRef(false)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const resizeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const leftColumnElementRef = useRef<HTMLElement | null>(null)
  const rightColumnElementRef = useRef<HTMLElement | null>(null)

  // Wheel scroll smoothing state
  const accumulatedDeltaRef = useRef<number>(0)
  const wheelRafIdRef = useRef<number | null>(null)
  const isWheelScrollingRef = useRef<boolean>(false)

  // Hover leave debounce state
  const leaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // React state for UI updates
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null)
  const [isKeyboardMode, setIsKeyboardMode] = useState(false)
  const isKeyboardModeRef = useRef(false)

  // Callback ref for when images finish loading (triggers animation setup)
  const onImagesLoadedRef = useRef<(() => void) | null>(null)

  // Use extracted hooks
  const prefersReducedMotion = usePrefersReducedMotion()
  const imagesLoadedRef = useImagesLoaded(trackRef, onImagesLoadedRef)

  // DEBUG: Log when images finish loading (via callback, not state change)
  // Note: This won't trigger a re-render since we're using refs

  // Sync keyboard mode to ref for use in event handler callbacks
  useLayoutEffect(() => {
    isKeyboardModeRef.current = isKeyboardMode
  }, [isKeyboardMode])

  // Sync selectedImageId to ref for use in event handler callbacks
  useLayoutEffect(() => {
    selectedImageIdRef.current = selectedImageId
  }, [selectedImageId])

  // ============================================================================
  // Animation Control Functions
  // ============================================================================

  const pauseAnimations = useCallback(() => {
    leftAnimStateRef.current?.animation.pause()
    rightAnimStateRef.current?.animation.pause()
  }, [])

  const resumeAnimations = useCallback(() => {
    leftAnimStateRef.current?.animation.play()
    rightAnimStateRef.current?.animation.play()
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
   */
  const applyWheelDelta = useCallback(() => {
    // Guard: ensure animations are initialized
    if (!leftAnimStateRef.current || !rightAnimStateRef.current) {
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
      if (!selectedImageIdRef.current && !isKeyboardModeRef.current) {
        isPausedRef.current = false
        resumeAnimations()
      }
      return
    }

    // Calculate portion to apply this frame (15% of remaining)
    const deltaToApply = delta * WHEEL_SMOOTHING_FACTOR
    
    // Convert pixels to animation time change
    const leftState = leftAnimStateRef.current
    const rightState = rightAnimStateRef.current
    
    const leftTimeChange = (deltaToApply / leftState.singleSetHeight) * leftState.duration
    const rightTimeChange = (deltaToApply / rightState.singleSetHeight) * rightState.duration

    // Apply to animations
    const leftCurrentTime = leftState.animation.currentTime
    const rightCurrentTime = rightState.animation.currentTime

    if (typeof leftCurrentTime === 'number') {
      leftState.animation.currentTime = wrapTime(leftCurrentTime + leftTimeChange, leftState.duration)
    }
    if (typeof rightCurrentTime === 'number') {
      rightState.animation.currentTime = wrapTime(rightCurrentTime + rightTimeChange, rightState.duration)
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
    isPausedRef.current = true
    pauseAnimations()
  }, [pauseAnimations, cancelWheelScroll])

  // Resume animation when exiting keyboard navigation mode
  const resumeFromKeyboard = useCallback(() => {
    setIsKeyboardMode(false)
    isKeyboardModeRef.current = false
    // Only resume if no image is selected (e.g., via hover)
    if (!selectedImageIdRef.current) {
      isPausedRef.current = false
      resumeAnimations()
    }
  }, [resumeAnimations])

  // Clear selection and resume scrolling
  const clearSelection = useCallback(() => {
    if (!selectedImageIdRef.current) return
    selectedImageIdRef.current = null
    setSelectedImageId(null)
    isPausedRef.current = false
    resumeAnimations()
  }, [resumeAnimations])

  // Handle image hover - highlight and pause
  const handleImageHover = useCallback((imageId: string) => {
    // Skip selection while dragging - content movement causes spurious mouseenter events
    if (isDraggingRef.current) return

    // Cancel any pending leave timeout (prevents flash when moving between cards)
    if (leaveTimeoutRef.current) {
      clearTimeout(leaveTimeoutRef.current)
      leaveTimeoutRef.current = null
    }

    if (selectedImageIdRef.current === imageId) return

    cancelWheelScroll() // Interrupt wheel scroll
    selectedImageIdRef.current = imageId
    setSelectedImageId(imageId)
    isPausedRef.current = true
    pauseAnimations()
  }, [pauseAnimations, cancelWheelScroll])

  // Select a card by ID (used when drag ends over a card)
  const selectCard = useCallback((imageId: string) => {
    if (selectedImageIdRef.current === imageId) return
    selectedImageIdRef.current = imageId
    setSelectedImageId(imageId)
    isPausedRef.current = true
    pauseAnimations()
  }, [pauseAnimations])

  // Handle image leave - deselect and resume (debounced to prevent flash when moving between cards)
  const handleImageLeave = useCallback((imageId?: string) => {
    // Skip clearSelection while dragging - content movement causes spurious mouseleave events
    if (isDraggingRef.current) return
    if (imageId && selectedImageIdRef.current !== imageId) return

    // Debounce the leave to allow time for entering another card
    leaveTimeoutRef.current = setTimeout(() => {
      leaveTimeoutRef.current = null
      clearSelection()
    }, HOVER_DEBOUNCE_MS)
  }, [clearSelection])

  // ============================================================================
  // Animation Creation
  // ============================================================================

  /**
   * Creates WAAPI animation for a column
   */
  const createColumnAnimation = useCallback((
    column: HTMLElement,
    singleSetHeight: number,
    speed: number,
    preservedCurrentTime?: number
  ): ColumnAnimationState => {
    // Duration in ms = distance / speed * 1000
    const duration = (singleSetHeight / speed) * 1000

    // Create animation from 0 to -singleSetHeight (scrolling up)
    const animation = column.animate(
      [
        { transform: 'translate3d(0, 0, 0)' },
        { transform: `translate3d(0, -${singleSetHeight}px, 0)` }
      ],
      {
        duration,
        iterations: Infinity,
        easing: 'linear'
      }
    )

    // Start from middle position for bidirectional scroll room, or preserve previous position
    const newCurrentTime = preservedCurrentTime !== undefined ? (preservedCurrentTime % duration) : (duration / 2)
    animation.currentTime = newCurrentTime

    return { animation, duration, singleSetHeight }
  }, [])

  /**
   * Initializes or reinitializes animations after height changes
   */
  const initializeAnimations = useCallback((
    leftColumn: HTMLElement,
    rightColumn: HTMLElement,
    isReinit: boolean = false
  ): boolean => {
    // Divide by number of duplicate sets to get original count
    const leftOriginalCount = Math.floor(leftColumn.children.length / duplicateSets)
    const rightOriginalCount = Math.floor(rightColumn.children.length / duplicateSets)

    if (leftOriginalCount === 0 || rightOriginalCount === 0) return false

    const leftSingleSetHeight = measureSingleSetHeight(leftColumn, leftOriginalCount)
    const rightSingleSetHeight = measureSingleSetHeight(rightColumn, rightOriginalCount)

    if (leftSingleSetHeight <= 0 || rightSingleSetHeight <= 0) return false

    // Capture current animation times before cancelling (for reinit)
    const leftPrevTime = isReinit && leftAnimStateRef.current ? (leftAnimStateRef.current.animation.currentTime as number | null) : undefined
    const rightPrevTime = isReinit && rightAnimStateRef.current ? (rightAnimStateRef.current.animation.currentTime as number | null) : undefined

    // Cancel existing animations before creating new ones
    leftAnimStateRef.current?.animation.cancel()
    rightAnimStateRef.current?.animation.cancel()

    // Create new animations (preserving position on reinit)
    leftAnimStateRef.current = createColumnAnimation(leftColumn, leftSingleSetHeight, scrollSpeed, leftPrevTime ?? undefined)
    rightAnimStateRef.current = createColumnAnimation(rightColumn, rightSingleSetHeight, scrollSpeed, rightPrevTime ?? undefined)

    // If currently paused (e.g., during hover), pause the new animations too
    if (isPausedRef.current) {
      pauseAnimations()
    }

    return true
  }, [scrollSpeed, duplicateSets, createColumnAnimation, pauseAnimations])

  // ============================================================================
  // Keyboard Handler Effect
  // ============================================================================

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedImageIdRef.current) {
        clearSelection()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [clearSelection])

  // ============================================================================
  // Animation Initialization Effect
  // ============================================================================

  useLayoutEffect(() => {
    // Function to attempt animation initialization
    // Called both on mount and when images finish loading
    const tryInitialize = () => {
      if (!containerRef.current || !trackRef.current || !enabled) return
      if (!imagesLoadedRef.current) return // Images not ready yet
      if (isInitializedRef.current) return // Already initialized
      if (prefersReducedMotion) {
        isInitializedRef.current = true
        return
      }

      const leftColumn = leftColumnRef?.current || trackRef.current.querySelector('[data-column="left"]')
      const rightColumn = rightColumnRef?.current || trackRef.current.querySelector('[data-column="right"]')

      if (!leftColumn || !rightColumn || !(leftColumn instanceof HTMLElement) || !(rightColumn instanceof HTMLElement)) {
        return
      }

      leftColumnElementRef.current = leftColumn
      rightColumnElementRef.current = rightColumn
      isInitializedRef.current = true

      if (!initializeAnimations(leftColumn, rightColumn)) {
        isInitializedRef.current = false
        return
      }

      // Setup ResizeObserver with proper debounce
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserverRef.current = new ResizeObserver(() => {
          // Clear any pending timeout to debounce rapid resize events
          if (resizeTimeoutRef.current) {
            clearTimeout(resizeTimeoutRef.current)
          }

          resizeTimeoutRef.current = setTimeout(() => {
            if (leftColumnElementRef.current && rightColumnElementRef.current && isInitializedRef.current) {
              initializeAnimations(leftColumnElementRef.current, rightColumnElementRef.current, true)
            }
          }, RESIZE_DEBOUNCE_MS)
        })

        resizeObserverRef.current.observe(leftColumn)
        resizeObserverRef.current.observe(rightColumn)
      }
    }

    // Set up callback for when images finish loading
    onImagesLoadedRef.current = tryInitialize

    // Try to initialize immediately (if images are already loaded)
    tryInitialize()

    return () => {
      // Clear the callback
      onImagesLoadedRef.current = null

      // Clear pending resize timeout to prevent memory leaks
      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current)
        resizeTimeoutRef.current = null
      }

      // Clear pending leave timeout
      if (leaveTimeoutRef.current) {
        clearTimeout(leaveTimeoutRef.current)
        leaveTimeoutRef.current = null
      }

      if (resizeObserverRef.current) {
        resizeObserverRef.current.disconnect()
        resizeObserverRef.current = null
      }

      leftAnimStateRef.current?.animation.cancel()
      rightAnimStateRef.current?.animation.cancel()
      leftAnimStateRef.current = null
      rightAnimStateRef.current = null
      isInitializedRef.current = false
      leftColumnElementRef.current = null
      rightColumnElementRef.current = null
    }
  }, [enabled, leftColumnRef, rightColumnRef, prefersReducedMotion, initializeAnimations, imagesLoadedRef])

  // ============================================================================
  // Pointer/Drag Events Effect
  // ============================================================================

  useLayoutEffect(() => {
    const container = containerRef.current
    // Note: We check imagesLoadedRef but don't depend on it - handlers will just
    // early-return if animations aren't initialized yet
    if (!container || !enabled || prefersReducedMotion) return

    let isPointerDown = false
    let isDragging = false
    let dragStartY = 0

    const handlePointerDown = (e: PointerEvent) => {
      // Ignore clicks on buttons (e.g., caption button)
      if ((e.target as HTMLElement).closest('button')) return

      isPointerDown = true
      isDragging = false
      dragStartY = e.clientY
    }

    const handlePointerMove = (e: PointerEvent) => {
      if (!isPointerDown) return
      if (!leftAnimStateRef.current || !rightAnimStateRef.current) return

      const deltaY = e.clientY - dragStartY

      // Start dragging after threshold to distinguish from clicks
      if (!isDragging && Math.abs(deltaY) > DRAG_THRESHOLD_PX) {
        isDragging = true
        isDraggingRef.current = true
        cancelWheelScroll() // Interrupt wheel scroll
        container.setPointerCapture(e.pointerId)
        pauseAnimations()
      }

      if (!isDragging) return

      // Convert pixel delta to time delta
      // Dragging down (positive deltaY) = content moves down = go backwards in animation (subtract from currentTime)
      // Dragging up (negative deltaY) = content moves up = go forwards in animation (add to currentTime)
      const leftState = leftAnimStateRef.current
      const rightState = rightAnimStateRef.current

      const leftTimeChange = (-deltaY / leftState.singleSetHeight) * leftState.duration
      const rightTimeChange = (-deltaY / rightState.singleSetHeight) * rightState.duration

      const leftCurrentTime = leftState.animation.currentTime
      const rightCurrentTime = rightState.animation.currentTime

      if (typeof leftCurrentTime === 'number') {
        leftState.animation.currentTime = wrapTime(leftCurrentTime + leftTimeChange, leftState.duration)
      }
      if (typeof rightCurrentTime === 'number') {
        rightState.animation.currentTime = wrapTime(rightCurrentTime + rightTimeChange, rightState.duration)
      }

      dragStartY = e.clientY
    }

    const handlePointerUp = (e: PointerEvent) => {
      if (!isPointerDown) return

      const wasDragging = isDragging
      isPointerDown = false
      isDragging = false
      isDraggingRef.current = false

      if (container.hasPointerCapture(e.pointerId)) {
        container.releasePointerCapture(e.pointerId)
      }

      if (!wasDragging) return

      // Drag is a mouse interaction - exit keyboard mode if it was accidentally entered
      // (clicking on gallery triggers focus, which enters keyboard mode)
      if (isKeyboardModeRef.current) {
        setIsKeyboardMode(false)
        isKeyboardModeRef.current = false
      }

      // Use elementFromPoint to detect what's under cursor when drag ends
      // This handles the case where mouseenter won't fire (mouse was already there)
      const elementUnderCursor = document.elementFromPoint(e.clientX, e.clientY)
      const cardUnderCursor = elementUnderCursor?.closest('[data-gallery-image]')

      if (cardUnderCursor) {
        // Drag ended over a card - select it
        const instanceId = cardUnderCursor.getAttribute('data-instance-id')
        if (instanceId) {
          selectCard(instanceId)
        }
        // Stay paused since we're over a card
      } else {
        // Drag ended outside a card or outside gallery - clear selection and resume
        if (selectedImageIdRef.current) {
          selectedImageIdRef.current = null
          setSelectedImageId(null)
        }
        isPausedRef.current = false
        resumeAnimations()
      }

      // Prevent click-through after drag release so drag doesn't trigger navigation.
      const preventClick = (clickEvent: Event) => {
        clickEvent.preventDefault()
        clickEvent.stopPropagation()
        container.removeEventListener('click', preventClick, true)
      }
      container.addEventListener('click', preventClick, true)
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
  }, [enabled, prefersReducedMotion, pauseAnimations, resumeAnimations, cancelWheelScroll, selectCard])

  // ============================================================================
  // Wheel Scroll Effect
  // ============================================================================

  useLayoutEffect(() => {
    const container = containerRef.current
    // Note: Handlers early-return if animations aren't initialized
    if (!container || !enabled || prefersReducedMotion) return

    const handleWheel = (e: WheelEvent) => {
      // Guard: only handle if animations are initialized
      if (!leftAnimStateRef.current || !rightAnimStateRef.current) return

      e.preventDefault()

      // Accumulate delta (additive, handles rapid events)
      accumulatedDeltaRef.current += e.deltaY
      isWheelScrollingRef.current = true

      // Pause auto-scroll while user is manually scrolling
      isPausedRef.current = true
      pauseAnimations()

      // Start rAF loop if not already running
      if (wheelRafIdRef.current === null) {
        wheelRafIdRef.current = requestAnimationFrame(applyWheelDelta)
      }
    }

    container.addEventListener('wheel', handleWheel, { passive: false })

    return () => {
      container.removeEventListener('wheel', handleWheel)
      cancelWheelScroll()
    }
  }, [enabled, prefersReducedMotion, pauseAnimations, applyWheelDelta, cancelWheelScroll])

  // ============================================================================
  // Mouse Leave Effect (Resume Animation)
  // ============================================================================

  useLayoutEffect(() => {
    const container = containerRef.current
    // Note: Handlers early-return if animations aren't initialized
    if (!container || !enabled || prefersReducedMotion) return

    const handleMouseLeave = () => {
      // Don't resume if wheel scrolling is still applying delta
      // The rAF loop will resume auto-scroll when delta drains
      if (isWheelScrollingRef.current) return

      // Cancel any pending leave timeout - we're exiting the gallery entirely
      // so no need to debounce, clear selection immediately
      if (leaveTimeoutRef.current) {
        clearTimeout(leaveTimeoutRef.current)
        leaveTimeoutRef.current = null
      }

      // Clear selection and resume if not in keyboard navigation mode
      if (selectedImageIdRef.current && !isKeyboardModeRef.current) {
        selectedImageIdRef.current = null
        setSelectedImageId(null)
      }

      // Resume animations if not in keyboard navigation mode
      if (!isKeyboardModeRef.current) {
        isPausedRef.current = false
        resumeAnimations()
      }
    }

    container.addEventListener('mouseleave', handleMouseLeave)

    return () => {
      container.removeEventListener('mouseleave', handleMouseLeave)
    }
  }, [enabled, prefersReducedMotion, resumeAnimations])

  // ============================================================================
  // Return
  // ============================================================================

  return {
    containerRef,
    trackRef,
    selectedImageId,
    handleImageHover,
    handleImageLeave,
    pauseForKeyboard,
    resumeFromKeyboard,
    isKeyboardMode,
  }
}
