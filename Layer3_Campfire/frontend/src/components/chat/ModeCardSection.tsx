// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useSessionStore } from '@/stores/sessionStore'
import { CHAT_MODE_OPTIONS, type ChatMode } from '@/lib/constants'
import styles from './ModeCardSection.module.css'

export function ModeCardSection() {
  const chatMode = useSessionStore((state) => state.chatMode)
  const setChatMode = useSessionStore((state) => state.setChatMode)

  return (
    <div className={styles.wrapper}>
      <div className={styles.separator} />
      <div className={styles.tabs}>
        {CHAT_MODE_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`${styles.tab} ${chatMode === option.value ? styles.selected : ''}`}
            onClick={() => setChatMode(option.value as ChatMode)}
            aria-pressed={chatMode === option.value}
            data-tooltip={option.description}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}
