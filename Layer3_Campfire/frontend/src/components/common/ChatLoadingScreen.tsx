// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import type { Ref } from 'react'
import styles from './ChatLoadingScreen.module.css'

interface ChatLoadingScreenProps {
  /** Whether the loading screen is visible */
  isVisible?: boolean
  /** Ref for parent animation control */
  ref?: Ref<HTMLDivElement>
}

/**
 * ChatLoadingScreen - Full viewport loading overlay with chat page styling
 * Uses triple dot animation with white + blue gradient background to match chat page
 * React 19: ref is now a regular prop, no forwardRef needed
 */
export function ChatLoadingScreen({ isVisible = true, ref }: ChatLoadingScreenProps) {
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