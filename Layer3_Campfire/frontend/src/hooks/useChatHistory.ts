// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useQuery, useQueries } from '@tanstack/react-query'
import { chatHistoryService } from '@/services/chatHistory'
import { showChatHistoryError } from '@/lib/toast'
import { getOrCreateAnonymousUserId } from '@/lib/anonymousUser'
import { useChatHistoryStore } from '@/stores/chatHistoryStore'
import type { ChatHistoryItem } from '@/stores/chatHistoryStore'
import type { Message } from '@/schemas/chat'

const MAX_CHATS = 50
const MAX_TITLE_LENGTH = 120
// Sidebar "expires at" hint only — backend Redis TTL is 30 days, so chats may live longer than this implies.
const CHAT_TTL_MS = 24 * 60 * 60 * 1000
const ORPHANED_STORAGE_KEY = 'reading-room-chat-history-orphaned'

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

const mergeChats = (
  apiChats: Array<{ chatId: string; mode?: string }>,
  existingChats: ChatHistoryItem[],
  nowMs: number
) : ChatHistoryItem[] => {
  const existingById = new Map(existingChats.map((chat) => [chat.chatId, chat]))
  const backendChats: ChatHistoryItem[] = apiChats.map(({ chatId, mode }) => {
    const existing = existingById.get(chatId)
    if (existing?.optimistic) {
      const { optimistic: _optimistic, optimisticUntil: _optimisticUntil, ...rest } = existing
      return { ...rest, optimistic: false, mode: mode ?? rest.mode }
    }
    return existing ? { ...existing, mode: mode ?? existing.mode } : { chatId, mode }
  })

  const backendIdSet = new Set(apiChats.map((c) => c.chatId))
  const optimisticHoldovers = existingChats.filter((chat) => {
    if (!chat.optimistic) return false
    if (backendIdSet.has(chat.chatId)) return false
    return !isOptimisticExpired(chat, nowMs)
  })

  return [...optimisticHoldovers, ...backendChats].slice(0, MAX_CHATS)
}

const areChatsEqual = (nextChats: ChatHistoryItem[], prevChats: ChatHistoryItem[]) => {
  if (nextChats.length !== prevChats.length) return false
  for (let i = 0; i < nextChats.length; i += 1) {
    const next = nextChats[i]
    const prev = prevChats[i]
    if (!prev) return false
    if (next.chatId !== prev.chatId) return false
    if (next.title !== prev.title) return false
    if (next.lastMessage !== prev.lastMessage) return false
    if (next.timestamp !== prev.timestamp) return false
    if (next.expiresAt !== prev.expiresAt) return false
    if (next.messageCount !== prev.messageCount) return false
    if (next.optimistic !== prev.optimistic) return false
    if (next.optimisticUntil !== prev.optimisticUntil) return false
  }
  return true
}

const getLastMessagePreview = (messages: Message[]) => {
  if (!messages.length) return ''
  const lastContent = messages[messages.length - 1]?.content ?? ''
  return getTitleFromMessage(lastContent)
}

export const useChatHistory = () => {
  const storedChats = useChatHistoryStore((state) => state.chats)
  const setChats = useChatHistoryStore((state) => state.setChats)
  const updateChat = useChatHistoryStore((state) => state.updateChat)
  const userId = getOrCreateAnonymousUserId()

  const chatListQuery = useQuery({
    queryKey: ['chat-history', 'list', userId],
    queryFn: () => chatHistoryService.getChats(),
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.removeItem(ORPHANED_STORAGE_KEY)
  }, [])

  useEffect(() => {
    if (chatListQuery.isError) {
      showChatHistoryError()
    }
  }, [chatListQuery.isError])

  const mergedChats = useMemo(() => {
    const nowMs = Date.now()
    if (chatListQuery.isError || !chatListQuery.data) {
      return storedChats
    }
    return mergeChats(chatListQuery.data, storedChats, nowMs)
  }, [chatListQuery.data, chatListQuery.isError, storedChats])

  useEffect(() => {
    if (!areChatsEqual(mergedChats, storedChats)) {
      setChats(mergedChats)
    }
  }, [mergedChats, setChats, storedChats])

  const missingTitleIds = useMemo(
    () =>
      mergedChats
        .filter((chat) => (!chat.title && !chat.lastMessage) || !chat.expiresAt)
        .map((chat) => chat.chatId),
    [mergedChats]
  )

  const titleQueries = useQueries({
    queries: missingTitleIds.map((chatId) => ({
      queryKey: ['chat-history', 'messages', userId, chatId],
      queryFn: () => chatHistoryService.getChatMessages(chatId),
      staleTime: Infinity,
      refetchOnMount: false,
      refetchOnReconnect: false,
      refetchOnWindowFocus: false,
      gcTime: CHAT_TTL_MS,
      enabled: !!chatId,
    })),
  })

  const hydratedTitlesRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    titleQueries.forEach((query, index) => {
      if (query.status !== 'success' || !query.data) return
      const chatId = missingTitleIds[index]
      if (!chatId || hydratedTitlesRef.current.has(chatId)) return

      const messages = query.data
      const updates: Partial<ChatHistoryItem> = {
        messageCount: messages.length,
      }
      const firstUserMessage = messages.find(
        (message) => message.role === 'user' && message.content.trim().length > 0
      )
      if (firstUserMessage?.content) {
        const title = getTitleFromMessage(firstUserMessage.content)
        if (title) {
          updates.title = title
        }
      }

      const lastMessage = getLastMessagePreview(messages)
      if (lastMessage) {
        updates.lastMessage = lastMessage
      }

      const expiresAt = getExpiresAtFromMessages(messages)
      if (expiresAt) {
        updates.expiresAt = expiresAt
      }

      hydratedTitlesRef.current.add(chatId)
      updateChat(chatId, updates)
    })
  }, [titleQueries, missingTitleIds, updateChat])

  return {
    chats: mergedChats,
    isLoading: chatListQuery.isLoading,
    refetch: chatListQuery.refetch,
  }
}
