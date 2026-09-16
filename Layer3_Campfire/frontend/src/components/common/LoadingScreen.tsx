// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import type { Ref } from 'react'
import styles from './LoadingScreen.module.css'

interface LoadingScreenProps {
  /** Whether the loading screen is visible */
  isVisible?: boolean
  /** Ref for parent animation control */
  ref?: Ref<HTMLDivElement>
}

/**
 * LoadingScreen - Full viewport loading overlay
 * Shows branding and spinner while assets load
 * React 19: ref is now a regular prop, no forwardRef needed
 */
export function LoadingScreen({ isVisible = true, ref }: LoadingScreenProps) {
  return (
    <div
      ref={ref}
      className={`${styles.container} ${!isVisible ? styles.hidden : ''}`}
      aria-hidden={!isVisible}
      aria-label="Loading"
    >
      <div className={styles.spinner}>
        <span className={styles.dot} />
        <span className={styles.dot} />
        <span className={styles.dot} />
      </div>
    </div>
  )
}
