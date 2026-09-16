// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { InlineIcon } from '@/components/ui'
import { useSessionStore, selectHasInput, selectHasBothInputs } from '@/stores/sessionStore'
import { useAutosizeTextarea } from '@/hooks/useAutosizeTextarea'
import styles from './SearchBar.module.css'

interface SearchBarProps {
  onSubmit?: () => void
}

/**
 * SearchBar - Dual input search ("I want to..." + "on topic...")
 * Displays selected action and topic from session store
 * Shows voice button when empty, submit button when has text
 */
export const SearchBar = ({ onSubmit }: SearchBarProps) => {
  const selectedAction = useSessionStore((state) => state.selectedAction)
  const selectedTopic = useSessionStore((state) => state.selectedTopic)
  const setSelectedAction = useSessionStore((state) => state.setSelectedAction)
  const setSelectedTopic = useSessionStore((state) => state.setSelectedTopic)
  const setSelectedPromptId = useSessionStore((state) => state.setSelectedPromptId)
  const hasText = useSessionStore(selectHasInput)
  const canSubmit = useSessionStore(selectHasBothInputs)
  const { textareaRef: actionInputRef, resizeTextarea: resizeActionInput } = useAutosizeTextarea(selectedAction || '', {
    minRows: 1,
    maxRows: 4,
  })
  const { textareaRef: topicInputRef, resizeTextarea: resizeTopicInput } = useAutosizeTextarea(selectedTopic || '', {
    minRows: 1,
    maxRows: 4,
  })

  const handleActionChange = (value: string) => {
    setSelectedAction(value || null)
    setSelectedPromptId(null) // Clear prompt selection when typing
  }

  const handleTopicChange = (value: string) => {
    setSelectedTopic(value || null)
    setSelectedPromptId(null) // Clear prompt selection when typing
  }

  const handleButtonClick = () => {
    if (canSubmit) {
      onSubmit?.()
    }
    // Voice input disabled - feature not yet supported
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && canSubmit) {
      e.preventDefault()
      onSubmit?.()
    }
  }

  return (
    <div className={styles.container}>
      <div className={styles.inputGroup}>
        <div className={styles.inputWrapper}>
          <textarea
            ref={actionInputRef}
            placeholder="I want to..."
            className={styles.input}
            aria-label="Select an action"
            value={selectedAction || ''}
            onChange={(e) => handleActionChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={resizeActionInput}
            rows={1}
          />
        </div>
        <div className={styles.inputWrapperRight}>
          <span className={styles.divider}>on</span>
          <textarea
            ref={topicInputRef}
            placeholder="enter a topic..."
            className={styles.input}
            aria-label="Enter a topic"
            value={selectedTopic || ''}
            onChange={(e) => handleTopicChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={resizeTopicInput}
            rows={1}
          />
          <button
            type="button"
            className={`${styles.voiceButton} ${hasText ? styles.submitButton : ''} ${!canSubmit ? styles.disabled : ''}`}
            onClick={handleButtonClick}
            disabled={!canSubmit}
            aria-label={hasText ? 'Submit' : 'Voice input (coming soon)'}
          >
            <InlineIcon name={hasText ? 'arrow-up' : 'microphone'} size={hasText ? 16 : 20} />
          </button>
        </div>
      </div>
    </div>
  )
}
