// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { Chip } from '@/components/ui'
import { useSessionStore } from '@/stores/sessionStore'
import { actions } from '@/mockdata'
import styles from './ActionChips.module.css'

export function ActionChips() {
  const selectedAction = useSessionStore((state) => state.selectedAction)
  const setSelectedAction = useSessionStore((state) => state.setSelectedAction)
  const setSelectedPromptId = useSessionStore((state) => state.setSelectedPromptId)

  const handleSelect = (action: typeof actions[number]) => {
    const newValue = selectedAction === action.label ? null : action.label
    setSelectedAction(newValue)
    setSelectedPromptId(null)
  }

  return (
    <div className={styles.container} role="group" aria-label="Action options">
      {actions.map((action) => (
        <Chip
          key={action.id}
          label={action.label}
          selected={selectedAction === action.label}
          onClick={() => handleSelect(action)}
        />
      ))}
    </div>
  )
}
