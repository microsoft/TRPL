// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useSessionStore, selectHasBothInputs } from '@/stores/sessionStore'
import { useAutosizeTextarea } from '@/hooks/useAutosizeTextarea'
import styles from './SearchBar.module.css'

interface ActionInputProps {
  onSubmit?: () => void
}

/**
 * ActionInput - First input ("I want to...")
 * Separated for mobile layout flexibility
 */
export const ActionInput = ({ onSubmit }: ActionInputProps) => {
  const selectedAction = useSessionStore((state) => state.selectedAction)
  const setSelectedAction = useSessionStore((state) => state.setSelectedAction)
  const setSelectedPromptId = useSessionStore((state) => state.setSelectedPromptId)
  const canSubmit = useSessionStore(selectHasBothInputs)
  const { textareaRef, resizeTextarea } = useAutosizeTextarea(selectedAction || '', {
    minRows: 1,
    maxRows: 4,
  })

  const handleActionChange = (value: string) => {
    setSelectedAction(value || null)
    setSelectedPromptId(null)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && canSubmit) {
      e.preventDefault()
      onSubmit?.()
    }
  }

  return (
    <div className={styles.inputWrapper}>
      <textarea
        ref={textareaRef}
        placeholder="I want to..."
        className={styles.input}
        aria-label="Select an action"
        value={selectedAction || ''}
        onChange={(e) => handleActionChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onInput={resizeTextarea}
        rows={1}
      />
    </div>
  )
}
