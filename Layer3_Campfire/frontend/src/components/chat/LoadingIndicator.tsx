'use client'

import { useState, useEffect } from 'react'
import { InlineIcon } from '@/components/ui'
import { TIMING } from '@/lib/constants'
import styles from './LoadingIndicator.module.css'

interface LoadingIndicatorProps {
  /** Dynamic progress text from the backend. Falls back to default if not provided. */
  progressText?: string | null
}

/**
 * LoadingIndicator - Displays animated progress text with cycling dots
 * Styled as a pill-shaped button with sparkle icon
 */
export function LoadingIndicator({ progressText }: LoadingIndicatorProps) {
  const [dotCount, setDotCount] = useState(1)

  useEffect(() => {
    const interval = setInterval(() => {
      setDotCount((prev) => (prev % 3) + 1)
    }, TIMING.LOADING_DOT_INTERVAL)

    return () => clearInterval(interval)
  }, [])

  const dots = '.'.repeat(dotCount)
  const displayText = progressText || 'Retrieving documents'

  return (
    <div className={styles.container} role="status" aria-live="polite">
      <InlineIcon name="loading" size={12} className={styles.icon} />
      <span className={styles.text}>
        {displayText}{dots}
      </span>
    </div>
  )
}
