import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Message } from '@/schemas/chat'
import { generateId, sanitizeInput, sanitizeAction, sanitizeTopic } from '@/lib/utils'
import type { ChatMode } from '@/lib/constants'

/**
 * Session store for managing conversation state
 * Chat session (messages, sessionId) persists to sessionStorage for reload support
 * Cleared when user navigates back to homepage (resetSession)
 */

interface SessionState {
  // Current session
  sessionId: string | null
  messages: Message[]

  // Chat UI state
  isLoading: boolean
  progressText: string | null
  chatMode: ChatMode
  recentChatHistoryVisibleDesktop: boolean
  recentChatHistoryVisibleMobile: boolean

  // UI state - selections (used for both value and visual highlighting)
  selectedAction: string | null
  selectedTopic: string | null
  selectedPromptId: string | null

  // Actions
  setSessionId: (id: string) => void
  addMessage: (message: Message) => void
  updateMessage: (id: string, updates: Partial<Message>) => void
  clearMessages: () => void
  setIsLoading: (loading: boolean) => void
  setProgressText: (text: string | null) => void
  setChatMode: (mode: ChatMode) => void
  setRecentChatHistoryVisibleDesktop: (visible: boolean) => void
  toggleRecentChatHistoryDesktop: () => void
  setRecentChatHistoryVisibleMobile: (visible: boolean) => void
  toggleRecentChatHistoryMobile: () => void
  setSelectedAction: (action: string | null) => void
  setSelectedTopic: (topic: string | null) => void
  setSelectedPromptId: (id: string | null) => void
  resetSession: () => void
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      // Initial state
      sessionId: null,
      messages: [],
      isLoading: false,
      progressText: null,
      chatMode: 'discovery',
      recentChatHistoryVisibleDesktop: false,
      recentChatHistoryVisibleMobile: false,
      selectedAction: null,
      selectedTopic: null,
      selectedPromptId: null,

      // Actions
      setSessionId: (id) => set({ sessionId: id }),

      addMessage: (message) =>
        set((state) => ({
          messages: [...state.messages, message],
        })),

      updateMessage: (id, updates) =>
        set((state) => ({
          messages: state.messages.map((msg) =>
            msg.id === id ? { ...msg, ...updates } : msg
          ),
        })),

      clearMessages: () => set({ messages: [] }),

      setIsLoading: (loading) => set({ isLoading: loading }),

      setProgressText: (text) => set({ progressText: text }),

      setChatMode: (mode) => set({ chatMode: mode }),

      setRecentChatHistoryVisibleDesktop: (visible) =>
        set({ recentChatHistoryVisibleDesktop: visible }),

      toggleRecentChatHistoryDesktop: () =>
        set((state) => ({
          recentChatHistoryVisibleDesktop: !state.recentChatHistoryVisibleDesktop,
        })),

      setRecentChatHistoryVisibleMobile: (visible) =>
        set({ recentChatHistoryVisibleMobile: visible }),

      toggleRecentChatHistoryMobile: () =>
        set((state) => ({
          recentChatHistoryVisibleMobile: !state.recentChatHistoryVisibleMobile,
        })),

      setSelectedAction: (action) => set({ selectedAction: action }),

      setSelectedTopic: (topic) => set({ selectedTopic: topic }),

      setSelectedPromptId: (id) => set({ selectedPromptId: id }),

      resetSession: () =>
        set({
          sessionId: null,
          messages: [],
          isLoading: false,
          progressText: null,
          chatMode: 'discovery',
          recentChatHistoryVisibleDesktop: false,
          recentChatHistoryVisibleMobile: false,
          selectedAction: null,
          selectedTopic: null,
          selectedPromptId: null,
        }),
    }),
    {
      name: 'reading-room-session',
      // Use sessionStorage so chat persists on reload but not across browser sessions
      storage: {
        getItem: (name) => {
          if (typeof window === 'undefined') return null
          try {
            const value = sessionStorage.getItem(name)
            return value ? JSON.parse(value) : null
          } catch {
            // Corrupted data — clear it and start fresh
            sessionStorage.removeItem(name)
            return null
          }
        },
        setItem: (name, value) => {
          if (typeof window === 'undefined') return
          sessionStorage.setItem(name, JSON.stringify(value))
        },
        removeItem: (name) => {
          if (typeof window === 'undefined') return
          sessionStorage.removeItem(name)
        },
      },
      // Only persist chat session data, not transient UI state
      // Cast required: Zustand's persist generic expects full state type from partialize,
      // but only these fields are persisted. Zustand merges with initial state on hydration.
      partialize: (state) => ({
        sessionId: state.sessionId,
        messages: state.messages,
        chatMode: state.chatMode,
      }) as SessionState,
    }
  )
)

// ============================================================================
// Selectors - Use with useSessionStore(selector)
// ============================================================================

/**
 * Selector: Compose a prompt from selected action and topic
 * Sanitizes values at compose time (not during typing) for clean display and submission
 * Matches UI layout: "{action} on {topic}"
 */
export const selectComposedPrompt = (state: SessionState): string | null => {
  const { selectedAction, selectedTopic } = state
  // Sanitize at compose time, not during typing
  const action = selectedAction ? sanitizeAction(selectedAction) : null
  const topic = selectedTopic ? sanitizeTopic(selectedTopic) : null
  if (!action && !topic) return null

  if (action && topic) {
    return `${action} on ${topic}`
  }
  return action || topic
}

/**
 * Selector: Check if there's any input text (action or topic)
 */
export const selectHasInput = (state: SessionState): boolean =>
  Boolean(state.selectedAction?.trim() || state.selectedTopic?.trim())

/**
 * Selector: Check if both inputs have text (required for submission)
 */
export const selectHasBothInputs = (state: SessionState): boolean =>
  Boolean(state.selectedAction?.trim() && state.selectedTopic?.trim())

// State selectors
export const selectMessages = (state: SessionState) => state.messages
export const selectIsLoading = (state: SessionState) => state.isLoading
export const selectProgressText = (state: SessionState) => state.progressText
export const selectChatMode = (state: SessionState) => state.chatMode
export const selectRecentChatHistoryVisibleDesktop = (state: SessionState) =>
  state.recentChatHistoryVisibleDesktop
export const selectRecentChatHistoryVisibleMobile = (state: SessionState) =>
  state.recentChatHistoryVisibleMobile
export const selectSelectedAction = (state: SessionState) => state.selectedAction
export const selectSelectedTopic = (state: SessionState) => state.selectedTopic
export const selectSelectedPromptId = (state: SessionState) => state.selectedPromptId

// Action selectors
export const selectAddMessage = (state: SessionState) => state.addMessage
export const selectUpdateMessage = (state: SessionState) => state.updateMessage
export const selectSetIsLoading = (state: SessionState) => state.setIsLoading
export const selectSetProgressText = (state: SessionState) => state.setProgressText
export const selectSetChatMode = (state: SessionState) => state.setChatMode
export const selectToggleRecentChatHistoryDesktop = (state: SessionState) =>
  state.toggleRecentChatHistoryDesktop
export const selectToggleRecentChatHistoryMobile = (state: SessionState) =>
  state.toggleRecentChatHistoryMobile
export const selectSetSelectedAction = (state: SessionState) => state.setSelectedAction
export const selectSetSelectedTopic = (state: SessionState) => state.setSelectedTopic
export const selectSetSelectedPromptId = (state: SessionState) => state.setSelectedPromptId

/**
 * Helper to create a user message with sanitized content
 */
export const createUserMessage = (content: string, isInitialQuery = false): Message => ({
  id: generateId(),
  role: 'user',
  content: sanitizeInput(content),
  timestamp: new Date().toISOString(),
  ...(isInitialQuery && { isInitialQuery }),
})
