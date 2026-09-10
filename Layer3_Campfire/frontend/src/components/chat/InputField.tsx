'use client'

import { InlineIcon } from '@/components/ui'
import { useAutosizeTextarea } from '@/hooks/useAutosizeTextarea'
import styles from './ChatView.module.css'

interface InputFieldProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  placeholder?: string
  disabled?: boolean
}

/**
 * InputField - Chat message input with submit button
 * Button is disabled when empty, active with orange background when has text
 */
export function InputField({
  value,
  onChange,
  onSubmit,
  placeholder = 'Ask anything...',
  disabled = false,
}: InputFieldProps) {
  const hasText = value.trim().length > 0
  const { textareaRef, resizeTextarea } = useAutosizeTextarea(value, {
    minRows: 1,
    maxRows: 6,
  })

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      // Don't submit if disabled (streaming in progress)
      if (!disabled) {
        onSubmit()
      }
    }
  }

  const handleButtonClick = () => {
    if (hasText) {
      onSubmit()
    }
    // Voice input disabled - feature not yet supported
  }

  return (
    <div className={styles.inputContainer}>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onInput={resizeTextarea}
        placeholder={placeholder}
        className={styles.input}
        aria-label="Chat message input"
        tabIndex={0}
        rows={1}
      />
      <button
        type="button"
        className={`${styles.submitButton} ${hasText ? styles.submitButtonActive : ''}`}
        onClick={handleButtonClick}
        aria-label={hasText ? 'Send message' : 'Voice input (coming soon)'}
        tabIndex={0}
        disabled={disabled || !hasText}
      >
        <InlineIcon name={hasText ? 'arrow-up' : 'microphone'} size={hasText ? 16 : 20} />
      </button>
    </div>
  )
}
