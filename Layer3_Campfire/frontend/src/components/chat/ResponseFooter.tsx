// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useState } from 'react'
import { InlineIcon } from '@/components/ui'
import { cn } from '@/lib/utils'
import styles from './ResponseFooter.module.css'

interface ResponseFooterProps {
  /** Number of sources/citations for this response */
  sourceCount: number
  /** Whether sources are currently displayed */
  sourcesOpen?: boolean
  /** Callback when "View Sources" is clicked */
  onViewSources?: () => void
  /** Callback when "Copy" is clicked */
  onCopy?: () => Promise<boolean> | boolean
  /** Callback when "Report Issue" is clicked */
  onReportIssue?: () => void
}

/**
 * ResponseFooter - Action bar shown at the bottom of assistant responses
 * Contains: View Sources button, Copy button, Info button
 */
export function ResponseFooter({
  sourceCount,
  sourcesOpen,
  onViewSources,
  onCopy,
  onReportIssue,
}: ResponseFooterProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    let copiedSuccessfully = false
    try {
      copiedSuccessfully = (await onCopy?.()) ?? false
    } catch {
      copiedSuccessfully = false
    }
    if (!copiedSuccessfully) return

    setCopied(true)
    setTimeout(() => setCopied(false), 1000)
  }
  return (
    <div className={styles.footer}>
      {sourceCount > 0 && (
        <button
          type="button"
          className={cn(styles.sourcesButton, sourcesOpen && styles.sourcesButtonActive)}
          onClick={onViewSources}
          aria-label={sourcesOpen ? 'Hide sources' : `View ${sourceCount} sources`}
        >
          <span>{sourcesOpen ? 'Hide Sources' : `View ${sourceCount} Sources`}</span>
          <InlineIcon
            name="plus"
            size={10}
            className={sourcesOpen ? styles.iconRotated : undefined}
          />
        </button>
      )}

      <button
        type="button"
        className={cn(styles.iconButton, copied && styles.iconButtonCopied)}
        onClick={handleCopy}
        aria-label={copied ? 'Copied' : 'Copy response'}
      >
        {copied ? (
          <span className={styles.checkmark} aria-hidden="true">✓</span>
        ) : (
          <InlineIcon name="copy" size={10} />
        )}
      </button>

      <button
        type="button"
        className={styles.iconButton}
        onClick={onReportIssue}
        aria-label="Report an issue"
      >
        <InlineIcon name="info" size={10} />
      </button>
    </div>
  )
}
