import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { chatHistoryService } from '@/services/chatHistory'
import { showChatHistoryError } from '@/lib/toast'
import type { Message } from '@/schemas/chat'

/**
 * Chat history store for managing list of previous chats
 * Integrated with backend API for persistence
 */

// Map backend format to frontend format
export interface ChatHistoryItem {
  chatId: string
  userId?: string
  lastMessage?: string
  timestamp?: Date | string
  expiresAt?: Date | string
  messageCount?: number
  title?: string
  mode?: string
  optimistic?: boolean
  optimisticUntil?: string
}

interface ChatHistoryState {
  chats: ChatHistoryItem[]
  isLoading: boolean

  // Actions
  loadChats: () => void
  setChats: (chats: ChatHistoryItem[]) => void
  addChat: (chat: ChatHistoryItem) => void
  updateChat: (chatId: string, updates: Partial<ChatHistoryItem>) => void
  removeChat: (chatId: string) => void
  cleanupExpiredChats: () => void
}

const MAX_CHATS = 50
const MAX_TITLE_LENGTH = 120
const OPTIMISTIC_TTL_MS = 5 * 60 * 1000
const CHAT_TTL_MS = 24 * 60 * 60 * 1000
const pendingTitleFetch = new Set<string>()

const getTitleFromMessage = (content: string) => {
  const trimmed = content.trim()
  if (!trimmed) return ''
  return trimmed.length > MAX_TITLE_LENGTH ? trimmed.slice(0, MAX_TITLE_LENGTH) : trimmed
}

const getExpiresAtFromMessages = (messages: Message[]) => {
  let latestMs = 0
  for (const message of messages) {
    const ts = Date.parse(message.timestamp)
    if (!Number.isNaN(ts) && ts > latestMs) {
      latestMs = ts
    }
  }
  if (!latestMs) return undefined
  return new Date(latestMs + CHAT_TTL_MS).toISOString()
}

const isOptimisticExpired = (chat: ChatHistoryItem, nowMs: number) => {
  if (!chat.optimistic) return false
  if (!chat.optimisticUntil) return false
  return new Date(chat.optimisticUntil).getTime() <= nowMs
}

export const useChatHistoryStore = create<ChatHistoryState>()(
  persist(
    (set, get) => ({
      // Initial state - no mock data, will load from API
      chats: [],
      isLoading: false,

      // Load chats from API
      loadChats: async () => {
        set({ isLoading: true })

        try {
          const apiChats = await chatHistoryService.getChats()
          const existingChats = get().chats
          const existingById = new Map(existingChats.map((chat) => [chat.chatId, chat]))
          const backendChats = apiChats.map(({ chatId, mode }) => {
            const existing = existingById.get(chatId)
            if (existing?.optimistic) {
              const { optimistic: _optimistic, optimisticUntil: _optimisticUntil, ...rest } = existing
              return { ...rest, mode: mode ?? rest.mode }
            }
            return existing ? { ...existing, mode: mode ?? existing.mode } : { chatId, mode }
          })

          const backendIdSet = new Set(apiChats.map((c) => c.chatId))
          const nowMs = Date.now()
          const optimisticHoldovers = existingChats.filter((chat) => {
            if (!chat.optimistic) return false
            if (backendIdSet.has(chat.chatId)) return false
            return !isOptimisticExpired(chat, nowMs)
          })

          const mergedChats = [...optimisticHoldovers, ...backendChats].slice(0, MAX_CHATS)

          set({
            chats: mergedChats,
            isLoading: false
          })

          const missingTitleIds = mergedChats
            .filter((chat) => (!chat.title && !chat.lastMessage) || !chat.expiresAt)
            .map((chat) => chat.chatId)

          if (missingTitleIds.length > 0) {
            const hydrateTitles = async () => {
              for (const chatId of missingTitleIds) {
                if (pendingTitleFetch.has(chatId)) continue
                pendingTitleFetch.add(chatId)
                try {
                  const messages = await chatHistoryService.getChatMessages(chatId)
                  const updates: Partial<ChatHistoryItem> = {}
                  const firstUserMessage = messages.find((message) => message.role === 'user')
                  if (firstUserMessage?.content) {
                    const title = getTitleFromMessage(firstUserMessage.content)
                    if (title) {
                      updates.title = title
                    }
                  }

                  const expiresAt = getExpiresAtFromMessages(messages)
                  if (expiresAt) {
                    updates.expiresAt = expiresAt
                  }

                  if (Object.keys(updates).length > 0) {
                    get().updateChat(chatId, updates)
                  }
                } catch (error) {
                  console.warn('Failed to hydrate chat title', error)
                } finally {
                  pendingTitleFetch.delete(chatId)
                }
              }
            }

            void hydrateTitles()
          }
        } catch (error) {
          console.warn('Chat history API not available, using local storage only:', error)
          showChatHistoryError()
          // Keep existing local history if API is not available
          set({
            isLoading: false
          })
        }
      },

      setChats: (chats) => set({ chats }),

      // Add new chat to history (FIFO with max limit)
      addChat: (chat) => {
        set((state) => {
          const withoutExisting = state.chats.filter((existing) => existing.chatId !== chat.chatId)
          const optimisticUntil =
            chat.optimistic && !chat.optimisticUntil
              ? new Date(Date.now() + OPTIMISTIC_TTL_MS).toISOString()
              : chat.optimisticUntil
          const normalizedChat = chat.optimistic
            ? { ...chat, optimisticUntil }
            : chat
          const newChats = [normalizedChat, ...withoutExisting]

          // Enforce max limit
          if (newChats.length > MAX_CHATS) {
            newChats.splice(MAX_CHATS)
          }

          return { chats: newChats }
        })
      },

      updateChat: (chatId, updates) =>
        set((state) => ({
          chats: state.chats.map((chat) =>
            chat.chatId === chatId ? { ...chat, ...updates } : chat
          ),
        })),

      // Remove specific chat
      removeChat: async (chatId) => {
        // Optimistically update UI
        set((state) => ({
          chats: state.chats.filter(chat => chat.chatId !== chatId)
        }))

        // Call API to delete
        try {
          await chatHistoryService.deleteChat(chatId)
        } catch (error) {
          console.error('Failed to delete chat:', error)
          // Reload chats on error to restore state
          get().loadChats()
        }
      },

      // Remove expired chats
      cleanupExpiredChats: () => {
        const now = new Date()
        const nowMs = now.getTime()
        set((state) => ({
          chats: state.chats.filter((chat) => {
            if (chat.expiresAt && new Date(chat.expiresAt) <= now) return false
            if (isOptimisticExpired(chat, nowMs)) return false
            return true
          })
        }))
      }
    }),
    {
      name: 'reading-room-chat-history',
      // Use localStorage for persistence across sessions
      storage: {
        getItem: (name) => {
          if (typeof window === 'undefined') return null
          try {
            const value = localStorage.getItem(name)
            return value ? JSON.parse(value) : null
          } catch {
            localStorage.removeItem(name)
            return null
          }
        },
        setItem: (name, value) => {
          if (typeof window === 'undefined') return
          localStorage.setItem(name, JSON.stringify(value))
        },
        removeItem: (name) => {
          if (typeof window === 'undefined') return
          localStorage.removeItem(name)
        },
      },
      // Only persist chats, not loading state
      partialize: (state) => ({
        chats: state.chats
      }) as ChatHistoryState
    }
  )
)

// Selectors
export const selectChats = (state: ChatHistoryState) => state.chats
export const selectIsLoading = (state: ChatHistoryState) => state.isLoading
export const selectAddChat = (state: ChatHistoryState) => state.addChat
export const selectUpdateChat = (state: ChatHistoryState) => state.updateChat
export const selectRemoveChat = (state: ChatHistoryState) => state.removeChat
export const selectCleanupExpiredChats = (state: ChatHistoryState) => state.cleanupExpiredChats
