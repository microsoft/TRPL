// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { z } from 'zod'

/**
 * Chat API schemas
 * Validates requests and responses to/from the Python RAG backend
 */

// Message role enum
export const MessageRoleSchema = z.enum(['user', 'assistant'])

// Single message schema
export const MessageSchema = z.object({
  id: z.string(),
  role: MessageRoleSchema,
  content: z.string(),
  timestamp: z.string().datetime(),
  // Optional: marks the initial user query (for semantic h1 rendering)
  isInitialQuery: z.boolean().optional(),
  // Optional: fact-checker verdict on the assistant's answer
  factCheck: z
    .object({
      flagged: z.boolean(),
      issues: z.array(
        z.object({
          claim: z.string(),
          explanation: z.string(),
        })
      ),
    })
    .optional(),
  // Optional: attached documents/images from RAG
  attachments: z
    .array(
      z.object({
        id: z.string(),
        type: z.enum(['image', 'document', 'letter']),
        title: z.string(),
        url: z.union([z.string().url(), z.literal('')]),
        thumbnail: z.string().url().optional(),
        // Backend fields (snake_case to match backend naming)
        source: z.enum(['letter', 'book']).optional(),
        trc_url: z.string().url().optional(),
        record_id: z.string().optional(),
        repository: z.string().optional(),
        collection: z.string().optional(),
        creation_date: z.string().optional(),
        book_title: z.string().optional(),
        book_authors: z.string().optional(),
        book_publisher: z.string().optional(),
        book_published_date: z.string().optional(),
        book_isbn: z.string().optional(),
        chapter_title: z.string().optional(),
        text: z.string().optional(),
        description: z.string().optional(),
        book_subjects: z.string().optional(),
        book_description: z.string().optional(),
        book_language: z.string().optional(),
        chapter_id: z.string().optional(),
        paragraph_ids: z.array(z.string()).optional(),
      })
    )
    .optional(),
})

// Chat request schema (what we send to the API)
export const ChatRequestSchema = z.object({
  message: z.string().min(1, 'Message cannot be empty'),
  sessionId: z.string(),
  userId: z.string().optional(),
  // Agent type: "default" or "experimental"
  agent: z.enum(['default', 'experimental']).optional(),
  // Persona mode — bound on the first message and locked for the session
  mode: z.enum(['discovery', 'research', 'teachers', 'students']).optional(),
  // Optional: context from selected topic/action
  context: z
    .object({
      action: z.string().optional(),
      topic: z.string().optional(),
    })
    .optional(),
})

// Chat response schema (what we receive from the API)
export const ChatResponseSchema = z.object({
  message: MessageSchema,
  sessionId: z.string(),
  // Suggested follow-up prompts
  suggestions: z.array(z.string()).optional(),
})

// Types inferred from schemas
export type MessageRole = z.infer<typeof MessageRoleSchema>
export type Message = z.infer<typeof MessageSchema>
export type ChatRequest = z.infer<typeof ChatRequestSchema>
export type ChatResponse = z.infer<typeof ChatResponseSchema>
