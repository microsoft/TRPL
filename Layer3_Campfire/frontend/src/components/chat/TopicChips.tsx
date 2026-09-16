// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { Chip } from '@/components/ui'
import { topics } from '@/mockdata'
import { useSessionStore } from '@/stores/sessionStore'
import { useIsMobile } from '@/hooks/useIsMobile'
import styles from './TopicChips.module.css'

const MOBILE_CHIP_COUNT = 4
const DESKTOP_CHIP_COUNT = 6

/**
 * TopicChips - Topic selection chips ("on [topic]...")
 * Displays topics like "Roosevelt's presidency", "Square Deal", etc.
 * Shows 4 chips on mobile, 6 on desktop/tablet
 */
export function TopicChips() {
  const selectedTopic = useSessionStore((state) => state.selectedTopic)
  const setSelectedTopic = useSessionStore((state) => state.setSelectedTopic)
  const setSelectedPromptId = useSessionStore((state) => state.setSelectedPromptId)

  const isMobile = useIsMobile()

  const visibleCount = isMobile ? MOBILE_CHIP_COUNT : DESKTOP_CHIP_COUNT
  const visibleTopics = topics.slice(0, visibleCount)

  const handleSelect = (topic: string) => {
    const newValue = selectedTopic === topic ? null : topic
    setSelectedTopic(newValue)
    setSelectedPromptId(null)
  }

  return (
    <div className={styles.container} role="group" aria-label="Topic options">
      {visibleTopics.map((topic) => (
        <Chip
          key={topic.id}
          label={topic.label}
          selected={selectedTopic === topic.label}
          onClick={() => handleSelect(topic.label)}
        />
      ))}
    </div>
  )
}
