// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import ReactMarkdown from 'react-markdown'
import { toast } from 'sonner'
import type { Message } from '@/schemas/chat'
import { useTypewriter } from '@/hooks/useTypewriter'
import { ResponseFooter } from './ResponseFooter'
import { ReportIssueModal } from './ReportIssueModal'
import { FactCheckBadge } from './FactCheckBadge'
import styles from './MessageBubble.module.css'
import { cn, copyTextToClipboard, isPdfUrl, toProxiedImageUrl, formatAttachmentsAsText } from '@/lib/utils'
import { useSessionStore } from '@/stores/sessionStore'
import {
  trackCitationOpened,
  trackResponseCopied,
  trackReportIssueOpened,
} from '@/lib/telemetry'
import { PdfThumbnail } from './PdfThumbnail'

function SourceThumbnail({ src, alt }: { src?: string; alt: string }) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) return null
  if (isPdfUrl(src)) {
    return <PdfThumbnail url={src} className={styles.sourceThumbnail} />
  }
  return (
    <img
      src={toProxiedImageUrl(src)}
      alt={alt}
      className={styles.sourceThumbnail}
      onError={() => setFailed(true)}
    />
  )
}

interface MessageBubbleProps {
  message: Message
  /** Whether this message is currently streaming (enables typewriter effect) */
  isStreaming?: boolean
  /** Callback fired as text streams in (for auto-scroll) */
  onProgress?: () => void
  /** Navigate with page leave animation (destination URL string) */
  onNavigate?: (destination: string) => void
}

/**
 * MessageBubble - Individual chat message
 * Assistant messages use typewriter effect during streaming, then render markdown
 * Backend returns markdown-formatted responses that need to be rendered
 */
export const MessageBubble = ({ message, isStreaming = false, onProgress, onNavigate }: MessageBubbleProps) => {
  const router = useRouter()
  const sessionId = useSessionStore((state) => state.sessionId)
  const isUser = message.role === 'user'
  const isAssistant = message.role === 'assistant'
  const [sourcesOpen, setSourcesOpen] = useState(
    () => message.role === 'assistant' && (message.attachments?.length ?? 0) > 0
  )
  const hasToggledSources = useRef(false)
  const [reportModalOpen, setReportModalOpen] = useState(false)

  // Use typewriter hook for assistant messages during streaming
  const { text: displayedContent, isAnimating } = useTypewriter(message.content, {
    enabled: !isUser && isStreaming,
    onProgress,
  })

  // Get source count from attachments
  const sourceCount = message.attachments?.length || 0

  // Preload source thumbnails into browser cache so they render instantly when expanded
  useEffect(() => {
    if (!message.attachments?.length) return
    const images: HTMLImageElement[] = []
    message.attachments.forEach((attachment) => {
      const src = attachment.thumbnail || attachment.url
      if (!src) return
      // Can't preload PDFs with new Image()
      if (isPdfUrl(src)) return
      const img = new Image()
      img.src = toProxiedImageUrl(src)
      images.push(img)
    })
    return () => {
      // Cancel pending image loads
      images.forEach(img => {
        img.src = ''
      })
    }
  }, [message.attachments])

  // Auto-open sources when attachments arrive for the first time (unless user toggled)
  useEffect(() => {
    if (hasToggledSources.current) return
    if (message.role !== 'assistant') return
    if ((message.attachments?.length ?? 0) > 0) {
      setSourcesOpen(true)
    }
  }, [message.attachments?.length, message.role])

  const sourcesListRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) return
    // Wait a frame so the browser lays out the newly mounted sources list
    requestAnimationFrame(() => {
      let el: HTMLElement | null = node.parentElement
      while (el) {
        const { overflowY } = getComputedStyle(el)
        const isScrollable = (overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight
        if (isScrollable) {
          const nodeRect = node.getBoundingClientRect()
          const containerRect = el.getBoundingClientRect()
          const targetScrollTop = el.scrollTop + nodeRect.bottom - containerRect.bottom + 100
          if (targetScrollTop > el.scrollTop) {
            el.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
          }
          return
        }
        el = el.parentElement
      }
    })
  }, [])

  return (
    <>
      <div className={cn(styles.bubble, isUser ? styles.user : styles.assistant)}>
        {isUser ? (
          <p className={styles.content}>{message.content}</p>
        ) : (
          <div className={styles.content}>
            <div
              className={cn(
                styles.markdownWrapper,
                !isStreaming && !isAnimating && message.factCheck?.flagged && styles.markdownWrapperFlagged,
              )}
            >
              <div
                className={cn(
                  styles.markdown,
                  !isStreaming && !isAnimating && message.factCheck?.flagged && styles.markdownFlagged,
                )}
              >
                <ReactMarkdown>{displayedContent}</ReactMarkdown>
              </div>
              {!isStreaming && !isAnimating && message.factCheck?.flagged && (
                <div className={styles.flaggedOverlay} aria-hidden="true" />
              )}
            </div>
            {isAssistant && !isStreaming && !isAnimating && message.factCheck?.flagged && (
              <FactCheckBadge
                flagged={message.factCheck.flagged}
                issues={message.factCheck.issues}
              />
            )}
            {isAssistant && !isStreaming && !isAnimating && (
              <div className={styles.footerEntrance}>
                <ResponseFooter
                  sourceCount={sourceCount}
                  sourcesOpen={sourcesOpen}
                  onViewSources={() => {
                    hasToggledSources.current = true
                    setSourcesOpen((prev) => !prev)
                  }}
                  onCopy={async () => {
                    const sourcesText = message.attachments?.length
                      ? formatAttachmentsAsText(message.attachments)
                      : ''
                    const copied = await copyTextToClipboard(message.content + sourcesText)
                    if (!copied) {
                      toast.error('Unable to copy response', {
                        description: 'Your browser blocked clipboard access. You can copy manually by selecting the text.',
                      })
                    } else {
                      trackResponseCopied({
                        sessionId: sessionId ?? undefined,
                        responseLength: message.content.length,
                      })
                    }
                    return copied
                  }}
                  onReportIssue={() => {
                    trackReportIssueOpened({ sessionId: sessionId ?? undefined })
                    setReportModalOpen(true)
                  }}
                />
                {sourcesOpen && message.attachments && message.attachments.length > 0 && (
                  <div ref={sourcesListRef} className={styles.sourcesList}>
                    {message.attachments.map((attachment, attachmentIndex) => (
                      <button
                        key={attachment.id}
                        className={styles.sourceItem}
                        onClick={() => {
                          const source = attachment.source || 'letter'
                          trackCitationOpened({
                            sessionId: sessionId ?? undefined,
                            sourceIndex: attachmentIndex + 1,
                            source,
                          })
                          const url = `/artifact/${encodeURIComponent(attachment.id)}?source=${source}&from=chat`
                          onNavigate ? onNavigate(url) : router.push(url)
                        }}
                      >
                        <SourceThumbnail
                          src={attachment.thumbnail || attachment.url}
                          alt={attachment.title || 'Source document'}
                        />
                        <div className={styles.sourceInfo}>
                          <span className={styles.sourceRepository}>
                            {attachment.collection || attachment.book_authors || attachment.repository || 'Unknown'}
                          </span>
                          <span className={styles.sourceTitle}>{attachment.title}</span>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      <ReportIssueModal open={reportModalOpen} onOpenChange={setReportModalOpen} />
    </>
  )
}
