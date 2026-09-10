'use client'

import { useState } from 'react'
import { Chip, InlineIcon } from '@/components/ui'
import styles from './SuggestedPrompts.module.css'

interface SuggestedPromptsProps {
  suggestions: readonly string[]
  onSelect: (prompt: string) => void
  initialVisibleCount?: number
  variant?: 'default' | 'dark'
}

/**
 * SuggestedPrompts - Clickable follow-up suggestions with "View more"
 * Shows limited prompts initially with option to expand
 * On mobile, shows only a "View prompt recommendations" button by default
 */
export function SuggestedPrompts({
  suggestions,
  onSelect,
  initialVisibleCount = 3,
  variant = 'default',
}: SuggestedPromptsProps) {
  const [showAll, setShowAll] = useState(false)
  const [mobileExpanded, setMobileExpanded] = useState(false)

  const handleViewMore = () => {
    setShowAll(true)
  }

  const handleMobileExpand = () => {
    setMobileExpanded(true)
  }

  const visibleSuggestions = showAll ? suggestions : suggestions.slice(0, initialVisibleCount)
  const hasMore = !showAll && suggestions.length > initialVisibleCount

  return (
    <div className={styles.container} role="group" aria-label="Suggested prompts">
      {/* Mobile: Show expand button when collapsed */}
      {!mobileExpanded && (
        <Chip
          label="View prompt recommendations"
          variant={variant === 'dark' ? 'dark' : 'default'}
          onClick={handleMobileExpand}
          className={styles.mobileExpandButton}
          tabIndex={0}
        >
          View prompt recommendations
          <InlineIcon name="plus" size={8} />
        </Chip>
      )}

      {/* Prompts - hidden on mobile until expanded */}
      <div className={`${styles.promptsWrapper} ${mobileExpanded ? styles.promptsWrapperExpanded : ''}`}>
        {visibleSuggestions.map((suggestion, index) => (
          <Chip
            key={index}
            label={suggestion}
            variant={variant === 'dark' ? 'dark' : 'default'}
            onClick={() => onSelect(suggestion)}
            tabIndex={0}
          />
        ))}
        {hasMore && (
          <Chip
            label="View more"
            variant={variant === 'dark' ? 'dark' : 'default'}
            onClick={handleViewMore}
            className={styles.viewMoreChip}
            tabIndex={0}
          >
            View more
            <InlineIcon name="plus" size={8} />
          </Chip>
        )}
      </div>
    </div>
  )
}
