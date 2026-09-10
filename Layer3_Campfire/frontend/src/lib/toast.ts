/**
 * Toast notification utilities
 * Centralized toast calls with consistent messaging and styling
 */

import { toast } from 'sonner'

let lastChatHistoryToastAt = 0
const CHAT_HISTORY_TOAST_COOLDOWN_MS = 15 * 60 * 1000

/**
 * Show a server/connection error toast.
 * Used when a backend request fails or the connection drops.
 */
export function showServerError() {
  toast.error('Connection Failed', {
    description: 'Please try again in a few moments.',
  })
}

/**
 * Show a chat history connection warning (rate-limited).
 */
export function showChatHistoryError(options?: { force?: boolean }) {
  const force = options?.force ?? false
  const now = Date.now()
  if (!force && now - lastChatHistoryToastAt < CHAT_HISTORY_TOAST_COOLDOWN_MS) return
  lastChatHistoryToastAt = now

  toast.error('Chat history unavailable', {
    description: 'You can keep chatting. We will retry automatically.',
  })
}
