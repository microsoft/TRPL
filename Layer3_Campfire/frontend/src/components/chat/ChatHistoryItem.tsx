// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import styles from './ChatHistoryItem.module.css'

interface ChatHistoryItemProps {
  chatId: string
  title: string
  timestamp?: Date | string
  expiresAt?: Date | string
  isActive: boolean
  onClick: (chatId: string) => void
  onHover?: (chatId: string) => void
}

/**
 * Individual chat item in the history sidebar
 * Shows title and expiration time
 */
export function ChatHistoryItem({
  chatId,
  title,
  timestamp: _timestamp, // eslint-disable-line @typescript-eslint/no-unused-vars
  expiresAt,
  isActive,
  onClick,
  onHover
}: ChatHistoryItemProps) {
  // Calculate time remaining
  const getTimeRemaining = (date: Date | string) => {
    const d = typeof date === 'string' ? new Date(date) : date
    const hours = Math.floor((d.getTime() - Date.now()) / (1000 * 60 * 60))
    if (hours <= 1) return { text: 'Expiring soon', isExpiringSoon: true }
    const hourText = hours === 1 ? 'hour' : 'hours'
    return { text: `${hours} ${hourText} remaining`, isExpiringSoon: false }
  }

  const handleClick = () => {
    onClick(chatId)
  }

  const handleHover = () => {
    onHover?.(chatId)
  }

  return (
    <button
      className={`${styles.chatItem} ${isActive ? styles.active : ''}`}
      onClick={handleClick}
      onMouseEnter={handleHover}
      onFocus={handleHover}
      aria-current={isActive ? 'page' : undefined}
    >
      <div className={styles.header}>
        <h3 className={styles.title}>{title}</h3>
      </div>


      {expiresAt && (
        <div className={styles.footer}>
          <span className={`${styles.expiry} ${getTimeRemaining(expiresAt).isExpiringSoon ? styles.expiringSoon : ''}`}>
            {getTimeRemaining(expiresAt).text}
          </span>
        </div>
      )}
    </button>
  )
}
