// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React from 'react'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useChatHistory } from '../useChatHistory'
import { chatHistoryService } from '@/services/chatHistory'
import { useChatHistoryStore } from '@/stores/chatHistoryStore'
import type { Message } from '@/schemas/chat'

// Mock the chat history service module
jest.mock('@/services/chatHistory', () => ({
  chatHistoryService: {
    getChats: jest.fn(),
    getChatMessages: jest.fn(),
    deleteChat: jest.fn(),
  },
}))

// Mock toast wrapper to avoid noise
jest.mock('@/lib/toast', () => ({
  showChatHistoryError: jest.fn(),
}))

// Mock anonymous user id helper for stable queryKey
jest.mock('@/lib/anonymousUser', () => ({
  getOrCreateAnonymousUserId: () => 'test-user',
}))

const mockGetChats = chatHistoryService.getChats as jest.MockedFunction<typeof chatHistoryService.getChats>
const mockGetChatMessages = chatHistoryService.getChatMessages as jest.MockedFunction<typeof chatHistoryService.getChatMessages>

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

const buildMessages = (chatId: string, firstUserText: string): Message[] => [
  {
    id: `${chatId}-0`,
    role: 'user',
    content: firstUserText,
    timestamp: new Date('2025-01-01T00:00:00.000Z').toISOString(),
  },
  {
    id: `${chatId}-1`,
    role: 'assistant',
    content: 'assistant reply',
    timestamp: new Date('2025-01-01T00:01:00.000Z').toISOString(),
  },
]

describe('useChatHistory', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    // Reset the chat history store before each test
    useChatHistoryStore.setState({ chats: [], isLoading: false })
  })

  it('hydrates titles when individual title queries resolve', async () => {
    // Backend returns the two chat ids; neither has a cached title yet
    mockGetChats.mockResolvedValue([{ chatId: 'chat-a' }, { chatId: 'chat-b' }])

    mockGetChatMessages.mockImplementation(async (chatId: string) => {
      if (chatId === 'chat-a') return buildMessages('chat-a', 'Hello A')
      if (chatId === 'chat-b') return buildMessages('chat-b', 'Hello B')
      return []
    })

    // Spy on updateChat to assert it gets called per chat with the derived title
    const updateChatSpy = jest.spyOn(useChatHistoryStore.getState(), 'updateChat')

    renderHook(() => useChatHistory(), { wrapper: createWrapper() })

    // After the list query resolves and the title queries resolve, updateChat
    // should fire for each chat with the title derived from the first user message.
    await waitFor(() => {
      expect(updateChatSpy).toHaveBeenCalledWith(
        'chat-a',
        expect.objectContaining({ title: 'Hello A' })
      )
      expect(updateChatSpy).toHaveBeenCalledWith(
        'chat-b',
        expect.objectContaining({ title: 'Hello B' })
      )
    })

    // The store should now reflect those titles
    const chats = useChatHistoryStore.getState().chats
    expect(chats.find((c) => c.chatId === 'chat-a')?.title).toBe('Hello A')
    expect(chats.find((c) => c.chatId === 'chat-b')?.title).toBe('Hello B')
  })

  it('does not call updateChat twice for the same chat', async () => {
    mockGetChats.mockResolvedValue([{ chatId: 'chat-a' }])
    mockGetChatMessages.mockResolvedValue(buildMessages('chat-a', 'Hello once'))

    const updateChatSpy = jest.spyOn(useChatHistoryStore.getState(), 'updateChat')

    const { rerender } = renderHook(() => useChatHistory(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(updateChatSpy).toHaveBeenCalledWith(
        'chat-a',
        expect.objectContaining({ title: 'Hello once' })
      )
    })

    const callsAfterFirstHydration = updateChatSpy.mock.calls.filter(
      (call) => call[0] === 'chat-a' && (call[1] as { title?: string }).title === 'Hello once'
    ).length

    // Force a re-render — the hydratedTitlesRef should prevent a duplicate updateChat
    act(() => {
      rerender()
    })

    const callsAfterRerender = updateChatSpy.mock.calls.filter(
      (call) => call[0] === 'chat-a' && (call[1] as { title?: string }).title === 'Hello once'
    ).length

    expect(callsAfterRerender).toBe(callsAfterFirstHydration)
  })
})
