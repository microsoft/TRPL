import { clsx, type ClassValue } from 'clsx'
import { type RefCallback, type MutableRefObject } from 'react'

/**
 * Merge class names with clsx
 * Usage: cn('base-class', isActive && 'active', className)
 */
export const cn = (...inputs: ClassValue[]): string => {
  return clsx(inputs)
}

/**
 * Format a date for display
 */
export const formatDate = (date: Date | string): string => {
  const d = typeof date === 'string' ? new Date(date) : date
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/**
 * Generate a unique ID
 */
export const generateId = (): string => {
  return Math.random().toString(36).substring(2, 15)
}

/**
 * Delay execution (useful for testing loading states)
 */
export const delay = (ms: number): Promise<void> => {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Input sanitization utilities
 * Prevents XSS, normalizes whitespace, limits length
 */

// Max lengths for different input types
export const INPUT_MAX_LENGTHS = {
  chatMessage: 4000,
  action: 200,
  topic: 200,
} as const

/**
 * Sanitize user input text
 * - Removes control characters (except newlines/tabs)
 * - Normalizes unicode whitespace
 * - Trims leading/trailing whitespace
 * - Limits to max length
 */
export const sanitizeInput = (
  input: string,
  maxLength: number = INPUT_MAX_LENGTHS.chatMessage
): string => {
  if (!input || typeof input !== 'string') return ''

  return (
    input
      // Remove control characters except newlines and tabs
      .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '')
      // Normalize unicode whitespace to regular spaces
      .replace(/[\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]/g, ' ')
      // Collapse multiple spaces into one
      .replace(/  +/g, ' ')
      // Trim
      .trim()
      // Limit length
      .slice(0, maxLength)
  )
}

/**
 * Sanitize action input (single line, shorter max)
 */
export const sanitizeAction = (input: string): string => {
  return sanitizeInput(input, INPUT_MAX_LENGTHS.action)
    // Actions should be single line
    .replace(/[\n\r]/g, ' ')
}

/**
 * Sanitize topic input (single line, shorter max)
 */
export const sanitizeTopic = (input: string): string => {
  return sanitizeInput(input, INPUT_MAX_LENGTHS.topic)
    // Topics should be single line
    .replace(/[\n\r]/g, ' ')
}

/**
 * Type for refs that can be merged
 */
export type ReactRef<T> = RefCallback<T> | MutableRefObject<T | null> | null | undefined

/**
 * Merge multiple refs into a single callback ref
 * Handles both callback refs and object refs safely without type casting
 *
 * @example
 * const mergedRef = mergeRefs(ref1, ref2, ref3)
 * <div ref={mergedRef} />
 */
export function mergeRefs<T>(...refs: ReactRef<T>[]): RefCallback<T> {
  return (node: T | null) => {
    refs.forEach((ref) => {
      if (!ref) return
      if (typeof ref === 'function') {
        ref(node)
      } else {
        ref.current = node
      }
    })
  }
}

type AttachmentForFormat = {
  title?: string
  collection?: string
  book_authors?: string
  repository?: string
  trc_url?: string
  chapter_title?: string
}

export function formatAttachmentsAsText(attachments: AttachmentForFormat[]): string {
  if (!attachments.length) return ''
  const lines = attachments.map((a, i) => {
    const origin = a.collection || a.book_authors || a.repository || 'Unknown'
    const title = a.title || 'Untitled'
    const chapterPart = a.chapter_title ? ` — Ch. "${a.chapter_title}"` : ''
    const urlPart = a.trc_url ? `\n   ${a.trc_url}` : ''
    return `${i + 1}. ${title}${chapterPart} (${origin})${urlPart}`
  })
  return `\n\nSources:\n${lines.join('\n')}`
}

/**
 * Format messages as plain text transcript
 */
export function formatTranscript(
  messages: Array<{ role: string; content: string; timestamp?: string; attachments?: AttachmentForFormat[] }>
): string {
  const header = `Theodore Roosevelt Reading Room - Conversation Transcript
Exported: ${new Date().toLocaleString()}
${'='.repeat(60)}

`

  const body = messages
    .map((msg) => {
      const role = msg.role === 'user' ? 'You' : 'Theodore'
      const sourcesText =
        msg.role === 'assistant' && msg.attachments?.length
          ? formatAttachmentsAsText(msg.attachments)
          : ''
      return `${role}:\n${msg.content}${sourcesText}\n`
    })
    .join('\n')

  return header + body
}

/**
 * Check if a URL points to a PDF file
 */
export const isPdfUrl = (url: string): boolean => {
  try {
    const { pathname } = new URL(url)
    return pathname.toLowerCase().endsWith('.pdf')
  } catch {
    return url.toLowerCase().endsWith('.pdf')
  }
}

/**
 * Rewrite an Azure Blob Storage image URL to the same-origin image proxy so the
 * browser never fetches `*.blob.core.windows.net` directly. Required under
 * Data Foundations zero-trust, where the public blob endpoint is disabled and
 * only the frontend App Service (inside the peered VNet) can reach it.
 *
 * URLs on any other host (e.g. `unapproved.example`) or local paths (`/assets/...`) are
 * returned unchanged. The proxy route enforces the strict host allowlist; this
 * helper only decides what to route through it.
 */
export const toProxiedImageUrl = (url: string): string => {
  try {
    const { hostname } = new URL(url)
    if (hostname.endsWith('.blob.core.windows.net')) {
      return `/api/artifacts/image?url=${encodeURIComponent(url)}`
    }
  } catch {
    // Relative or malformed URL — leave it as-is.
  }
  return url
}

/**
 * Download text content as a file
 */
export function downloadTextFile(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)

  try {
    a.click()
  } finally {
    document.body.removeChild(a)
    // Keep the Blob URL alive until the browser has accepted the download.
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

/**
 * Copy text to clipboard with fallback for browsers where Clipboard API is blocked/unavailable.
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof window === 'undefined') return false

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fall through to execCommand fallback.
  }

  if (typeof document === 'undefined') return false

  let textarea: HTMLTextAreaElement | null = null
  try {
    textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.setAttribute('aria-hidden', 'true')
    textarea.style.position = 'fixed'
    textarea.style.top = '0'
    textarea.style.left = '-9999px'
    textarea.style.opacity = '0'

    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)

    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    if (textarea?.parentNode) {
      textarea.parentNode.removeChild(textarea)
    }
  }
}
