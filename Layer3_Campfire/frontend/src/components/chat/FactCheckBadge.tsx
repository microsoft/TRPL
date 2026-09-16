// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip'
import styles from './FactCheckBadge.module.css'

interface FactCheckBadgeProps {
  flagged: boolean
  issues: Array<{ claim: string; explanation: string }>
}

export function FactCheckBadge({ flagged, issues }: FactCheckBadgeProps) {
  if (!flagged || issues.length === 0) return null

  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger
        render={
          <span
            className={styles.badge}
            role="img"
            aria-label="Fact-checker flagged this answer"
            tabIndex={0}
          >
            !
          </span>
        }
      />
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner
          sideOffset={8}
          collisionPadding={16}
          className={styles.positioner}
        >
          <BaseTooltip.Popup className={styles.popup}>
            <div className={styles.popupHeader}>Fact-checker flagged:</div>
            <ul className={styles.issueList}>
              {issues.map((issue, i) => (
                <li key={i} className={styles.issueItem}>
                  <span className={styles.issueClaim}>{issue.claim}</span>
                  <span className={styles.issueExplanation}>{issue.explanation}</span>
                </li>
              ))}
            </ul>
          </BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  )
}
