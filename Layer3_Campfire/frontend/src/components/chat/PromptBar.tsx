// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useState } from 'react'
import { examplePrompts, type ExamplePrompt } from '@/mockdata'
import { useSessionStore } from '@/stores/sessionStore'
import { Chip } from '@/components/ui'
import styles from './PromptBar.module.css'

interface PromptBarProps {
  onPromptSelect?: (prompt: string) => void
}

/**
 * PromptBar - Bottom bar with example prompts
 * Shows clickable example queries that auto-fill the search inputs
 */
export function PromptBar({ onPromptSelect }: PromptBarProps) {
  const selectedPromptId = useSessionStore((state) => state.selectedPromptId)
  const setSelectedAction = useSessionStore((state) => state.setSelectedAction)
  const setSelectedTopic = useSessionStore((state) => state.setSelectedTopic)
  const setSelectedPromptId = useSessionStore((state) => state.setSelectedPromptId)

  const [visibleCount, setVisibleCount] = useState(3)

  const handlePromptClick = (prompt: ExamplePrompt) => {
    // Fill inputs with prompt data (also highlights matching chips)
    setSelectedAction(prompt.action)
    setSelectedTopic(prompt.topic)
    // Set this prompt as active
    setSelectedPromptId(prompt.id)
    onPromptSelect?.(prompt.text)
  }

  const handleViewMore = () => {
    setVisibleCount(examplePrompts.length)
  }

  const visiblePrompts = examplePrompts.slice(0, visibleCount)
  const hasMore = visibleCount < examplePrompts.length

  return (
    <div className={styles.container}>
      <div className={styles.promptList} role="group" aria-label="Example prompts">
        {visiblePrompts.map((prompt) => (
          <Chip
            key={prompt.id}
            label={prompt.text}
            variant="muted"
            selected={selectedPromptId === prompt.id}
            onClick={() => handlePromptClick(prompt)}
          />
        ))}
        {hasMore && (
          <Chip
            label="View More +"
            variant="muted"
            onClick={handleViewMore}
            className={styles.viewMoreButton}
          />
        )}
      </div>
    </div>
  )
}
