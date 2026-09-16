// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useState, useEffect } from 'react'

/**
 * usePrefersReducedMotion - Hook to detect user's reduced motion preference
 *
 * Returns true if the user prefers reduced motion, false otherwise.
 * Updates reactively if the user changes their system preference.
 *
 * Usage:
 * ```tsx
 * const prefersReducedMotion = usePrefersReducedMotion()
 *
 * if (prefersReducedMotion) {
 *   // Skip or simplify animations
 * }
 * ```
 */
export function usePrefersReducedMotion(): boolean {
  // Default to false during SSR, will be updated on mount
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')

    // Set initial value
    setPrefersReducedMotion(mediaQuery.matches)

    // Listen for changes
    const handleChange = (event: MediaQueryListEvent) => {
      setPrefersReducedMotion(event.matches)
    }

    mediaQuery.addEventListener('change', handleChange)

    return () => {
      mediaQuery.removeEventListener('change', handleChange)
    }
  }, [])

  return prefersReducedMotion
}

/**
 * getPrefersReducedMotion - Non-reactive check for reduced motion preference
 *
 * Use this for one-time checks (e.g., in useEffect callbacks or event handlers)
 * where you don't need reactive updates.
 *
 * Note: Returns false during SSR. Only call in browser context.
 */
export function getPrefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
