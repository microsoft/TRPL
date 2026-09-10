'use client'

import { InlineIcon } from '@/components/ui'
import { useSessionStore, selectHasInput, selectHasBothInputs } from '@/stores/sessionStore'
import { useAutosizeTextarea } from '@/hooks/useAutosizeTextarea'
import styles from './SearchBar.module.css'

interface TopicInputProps {
  onSubmit?: () => void
}

/**
 * TopicInput - Second input ("on enter a topic...")
 * Separated for mobile layout flexibility
 * Shows voice button when empty, submit button when has text
 */
export const TopicInput = ({ onSubmit }: TopicInputProps) => {
  const selectedTopic = useSessionStore((state) => state.selectedTopic)
  const setSelectedTopic = useSessionStore((state) => state.setSelectedTopic)
  const setSelectedPromptId = useSessionStore((state) => state.setSelectedPromptId)
  const hasText = useSessionStore(selectHasInput)
  const canSubmit = useSessionStore(selectHasBothInputs)
  const { textareaRef, resizeTextarea } = useAutosizeTextarea(selectedTopic || '', {
    minRows: 1,
    maxRows: 4,
  })

  const handleTopicChange = (value: string) => {
    setSelectedTopic(value || null)
    setSelectedPromptId(null)
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
    <div className={styles.inputWrapperRight}>
      <span className={styles.divider}>on</span>
      <textarea
        ref={textareaRef}
        placeholder="enter a topic..."
        className={styles.input}
        aria-label="Enter a topic"
        value={selectedTopic || ''}
        onChange={(e) => handleTopicChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onInput={resizeTextarea}
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
  )
}
