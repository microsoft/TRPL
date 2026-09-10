'use client'

import { ChatView } from '@/components/chat'
import { ErrorBoundary } from '@/components/ui'

/**
 * Chat Interface Page
 */
export default function ChatPage() {
  return (
    <ErrorBoundary>
      <ChatView />
    </ErrorBoundary>
  )
}
