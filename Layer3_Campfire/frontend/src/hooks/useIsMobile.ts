// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useSyncExternalStore, useCallback } from 'react'

const MOBILE_BREAKPOINT = 768
const DEBOUNCE_MS = 150

/**
 * Creates a debounced function that delays invoking func until after wait ms
 */
function debounce<T extends (...args: unknown[]) => void>(
  func: T,
  wait: number
): T {
  let timeoutId: ReturnType<typeof setTimeout> | null = null

  return ((...args: Parameters<T>) => {
    if (timeoutId) {
      clearTimeout(timeoutId)
    }
    timeoutId = setTimeout(() => {
      func(...args)
      timeoutId = null
    }, wait)
  }) as T
}

/**
 * Store for tracking viewport width changes
 * Uses a singleton pattern to share listeners across hook instances
 */
const viewportStore = {
  listeners: new Set<() => void>(),

  subscribe(callback: () => void): () => void {
    // Create debounced callback for resize events
    const debouncedCallback = debounce(callback, DEBOUNCE_MS)

    // Add to listeners set
    this.listeners.add(debouncedCallback)

    // Add resize listener if this is the first subscriber
    if (this.listeners.size === 1) {
      window.addEventListener('resize', this.notifyAll)
    }

    // Return cleanup function
    return () => {
      this.listeners.delete(debouncedCallback)
      if (this.listeners.size === 0) {
        window.removeEventListener('resize', this.notifyAll)
      }
    }
  },

  notifyAll(): void {
    viewportStore.listeners.forEach((listener) => listener())
  },

  getSnapshot(): boolean {
    return window.innerWidth < MOBILE_BREAKPOINT
  },

  getServerSnapshot(): boolean {
    // Default to desktop on server - most common case
    // This prevents layout shift for majority of users
    return false
  },
}

/**
 * Hook to detect if the viewport is mobile-sized
 *
 * Uses useSyncExternalStore for proper SSR hydration handling
 * and debounced resize events for performance
 *
 * @returns true when viewport width is less than 768px
 */
export function useIsMobile(): boolean {
  // useCallback required here - useSyncExternalStore needs stable subscribe reference
  const subscribe = useCallback(
    (callback: () => void) => viewportStore.subscribe(callback),
    []
  )

  return useSyncExternalStore(
    subscribe,
    viewportStore.getSnapshot,
    viewportStore.getServerSnapshot
  )
}
