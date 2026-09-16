// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { SearchBar, ActionInput, TopicInput } from '@/components/search'
import { ActionChips } from './ActionChips'
import { TopicChips } from './TopicChips'
import { ModeCardSection } from './ModeCardSection'
import { useIsMobile } from '@/hooks/useIsMobile'
import { useSessionStore, selectComposedPrompt, createUserMessage } from '@/stores/sessionStore'
import styles from './ChatBox.module.css'

interface ChatBoxProps {
  onSubmit?: () => void
}

export function ChatBox({ onSubmit }: ChatBoxProps) {
  const isMobile = useIsMobile()
  const composedPrompt = useSessionStore(selectComposedPrompt)
  const addMessage = useSessionStore((state) => state.addMessage)

  const handleSubmit = () => {
    if (composedPrompt) {
      // Add user message to store before navigating to chat
      addMessage(createUserMessage(composedPrompt, true))
      // Call parent's onSubmit to trigger transition
      onSubmit?.()
    }
  }

  // Mobile layout: input → chips → input → chips (stacked)
  if (isMobile) {
    return (
      <div className={styles.container}>
        <div className={styles.mobileSection}>
          <ActionInput onSubmit={handleSubmit} />
          <ActionChips />
        </div>
        <div className={styles.mobileSection}>
          <TopicInput onSubmit={handleSubmit} />
          <TopicChips />
        </div>
        <div className={styles.modeRow}>
          <ModeCardSection />
        </div>
      </div>
    )
  }

  // Desktop layout: side-by-side columns
  return (
    <div className={styles.container}>
      <SearchBar onSubmit={handleSubmit} />
      <div className={styles.chipsSection}>
        <div className={styles.chipsColumn}>
          <ActionChips />
        </div>
        <div className={styles.chipsColumn}>
          <TopicChips />
        </div>
      </div>
      <div className={styles.modeRow}>
        <ModeCardSection />
      </div>
    </div>
  )
}
