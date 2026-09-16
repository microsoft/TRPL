// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import type { Message } from '@/schemas/chat'
import { getOrCreateAnonymousUserId } from '@/lib/anonymousUser'

type ChatHistoryResponse = Array<{ chat_id: string; mode: string }>

interface BackendCitation {
  id?: string
  source?: 'letter' | 'book'
  title?: string
  trc_url?: string
  trpl_file_url?: string[]
  record_id?: string
  repository?: string
  collection?: string
  creation_date?: string
  book_title?: string
  book_authors?: string
  book_publisher?: string
  book_published_date?: string
  book_isbn?: string
  chapter_title?: string
  book_subjects?: string
  book_description?: string
  book_language?: string
  chapter_id?: string
  paragraph_ids?: string[]
  text?: string
  description?: string
  [key: string]: unknown
}

type ChatMessagesResponse = Array<{
  role: 'user' | 'assistant'
  text: string
  timestamp: number
  citations?: BackendCitation[] | null
}>

const getUserId = () => getOrCreateAnonymousUserId()
const API_BASE = '/api/chat-history'

/**
 * Chat History Service - Frontend API client for chat history
 */
export class ChatHistoryService {
  /**
   * Get all chats for the current user
   */
  async getChats(): Promise<Array<{ chatId: string; mode?: string }>> {
    const response = await fetch(
      `${API_BASE}/${encodeURIComponent(getUserId())}`,
      {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Failed to fetch chat history: ${response.statusText}`)
    }

    const data: ChatHistoryResponse = await response.json()
    return data.map((item) => ({ chatId: item.chat_id, mode: item.mode }))
  }

  /**
   * Get messages for a specific chat
   */
  async getChatMessages(chatId: string, signal?: AbortSignal): Promise<Message[]> {
    const response = await fetch(
      `${API_BASE}/${encodeURIComponent(getUserId())}/${encodeURIComponent(chatId)}/messages`,
      {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
        signal,
      }
    )

    if (!response.ok) {
      throw new Error(`Failed to fetch chat messages: ${response.statusText}`)
    }

    const data: ChatMessagesResponse = await response.json()

    // Convert backend format to frontend Message format
    return data.map((msg, index) => ({
      id: `${chatId}-${index}`,
      role: msg.role,
      content: msg.text,
      timestamp: toIsoTimestamp(msg.timestamp),
      attachments: transformCitations(msg.citations ?? [], chatId, index),
    }))
  }

  /**
   * Delete a chat from history
   */
  async deleteChat(chatId: string): Promise<boolean> {
    try {
      const response = await fetch(
        `${API_BASE}/${encodeURIComponent(getUserId())}/${encodeURIComponent(chatId)}`,
        {
          method: 'DELETE',
          headers: {
            'Content-Type': 'application/json',
          },
        }
      )

      if (!response.ok) {
        console.error('Failed to delete chat:', response.statusText)
        return false
      }

      return true
    } catch (error) {
      console.error('Error deleting chat:', error)
      return false
    }
  }
}

const toIsoTimestamp = (timestamp: number) => {
  const ms = timestamp > 1e12 ? timestamp : timestamp * 1000
  return new Date(ms).toISOString()
}

type Attachment = NonNullable<Message['attachments']>[number]

const transformCitations = (
  citations: BackendCitation[],
  chatId: string,
  messageIndex: number
): Attachment[] =>
  citations
    .filter((citation) => citation.source === 'book' || citation.trpl_file_url?.length || citation.trc_url)
    .map((citation, citationIndex) => {
      const isLetter = citation.source === 'letter'
      const imageUrl = citation.trpl_file_url?.[0]
      const url = imageUrl || citation.trc_url || ''

      return {
        id: citation.id || `citation-${chatId}-${messageIndex}-${citationIndex}`,
        type: (isLetter ? 'letter' : 'document') as Attachment['type'],
        title: citation.title || citation.book_title || 'Untitled',
        url,
        ...(citation.trpl_file_url?.[1] && { thumbnail: citation.trpl_file_url[1] }),
        ...(citation.source && { source: citation.source }),
        ...(citation.trc_url && { trc_url: citation.trc_url }),
        ...(citation.record_id && { record_id: citation.record_id }),
        ...(citation.repository && { repository: citation.repository }),
        ...(citation.collection && { collection: citation.collection }),
        ...(citation.creation_date && { creation_date: citation.creation_date }),
        ...(citation.book_title && { book_title: citation.book_title }),
        ...(citation.book_authors && { book_authors: citation.book_authors }),
        ...(citation.book_publisher && { book_publisher: citation.book_publisher }),
        ...(citation.book_published_date && { book_published_date: citation.book_published_date }),
        ...(citation.book_isbn && { book_isbn: citation.book_isbn }),
        ...(citation.chapter_title && { chapter_title: citation.chapter_title }),
        ...(citation.text && { text: citation.text }),
        ...(citation.description && { description: citation.description }),
        ...(citation.book_subjects && { book_subjects: citation.book_subjects }),
        ...(citation.book_description && { book_description: citation.book_description }),
        ...(citation.book_language && { book_language: citation.book_language }),
        ...(citation.chapter_id && { chapter_id: citation.chapter_id }),
        ...(citation.paragraph_ids && { paragraph_ids: citation.paragraph_ids }),
      } satisfies Attachment
    })
    .filter((attachment) => attachment.url || attachment.source === 'book')

// Export singleton instance
export const chatHistoryService = new ChatHistoryService()
