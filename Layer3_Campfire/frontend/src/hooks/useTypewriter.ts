'use client'

import { useState, useEffect, useRef } from 'react'
import { TIMING } from '@/lib/constants'

interface UseTypewriterOptions {
  /** Speed in milliseconds per character (lower = faster) */
  speed?: number
  /** Whether to animate. If false, returns full text immediately */
  enabled?: boolean
  /** Callback fired as text is revealed (for auto-scroll), throttled to rAF */
  onProgress?: () => void
}

interface UseTypewriterResult {
  /** The portion of text currently revealed */
  text: string
  /** Whether the typewriter is still animating (streaming or finishing catchup) */
  isAnimating: boolean
}

/**
 * useTypewriter - Hook that reveals text character by character
 * Streaming-aware: handles growing text without resetting
 * When streaming ends (enabled goes true→false), animates remaining text at 3x speed
 * Returns the revealed text and whether animation is still in progress
 */
export function useTypewriter(
  text: string,
  { speed = TIMING.TYPEWRITER_SPEED, enabled = true, onProgress }: UseTypewriterOptions = {}
): UseTypewriterResult {
  // Track how many characters have been revealed
  const revealedCountRef = useRef(0)
  const [revealedText, setRevealedText] = useState(enabled ? '' : text)
  const [isAnimating, setIsAnimating] = useState(false)
  // Throttle onProgress to animation frames to prevent scroll stutter
  const rafPendingRef = useRef(false)
  // Track enabled transitions for finishing animation
  const wasEnabledRef = useRef(false)
  const isFinishingRef = useRef(false)

  useEffect(() => {
    // Detect transition from enabled → disabled (stream ended)
    const justDisabled = wasEnabledRef.current && !enabled
    wasEnabledRef.current = enabled

    // If stream just ended and there's unrevealed text, enter finishing mode
    if (justDisabled && revealedCountRef.current < text.length) {
      isFinishingRef.current = true
      // isAnimating stays true (was set true when enabled was true)
    }

    // If enabled, we're actively animating
    if (enabled) {
      setIsAnimating(true)
    }

    // If not enabled and not finishing, show full text immediately
    if (!enabled && !isFinishingRef.current) {
      setRevealedText(text)
      revealedCountRef.current = text.length
      setIsAnimating(false)
      return
    }

    // If text shrinks (e.g., reset), reset our counter
    if (text.length < revealedCountRef.current) {
      revealedCountRef.current = 0
    }

    // In finishing mode, reveal multiple chars per tick for catchup (3x speed)
    const charsPerTick = isFinishingRef.current ? TIMING.TYPEWRITER_CATCHUP_CHARS : 1

    // Start interval to reveal characters
    const intervalId = setInterval(() => {
      if (revealedCountRef.current < text.length) {
        // Reveal next character(s)
        revealedCountRef.current = Math.min(
          revealedCountRef.current + charsPerTick,
          text.length
        )
        setRevealedText(text.slice(0, revealedCountRef.current))

        // Throttle onProgress to rAF to prevent scroll stutter on long responses
        if (onProgress && !rafPendingRef.current) {
          rafPendingRef.current = true
          requestAnimationFrame(() => {
            rafPendingRef.current = false
            onProgress()
          })
        }
      } else if (isFinishingRef.current) {
        // Finishing animation complete
        isFinishingRef.current = false
        setIsAnimating(false)
      }
      // Keep interval running - streaming might add more text
    }, speed)

    return () => clearInterval(intervalId)
  }, [text, speed, enabled, onProgress])

  return { text: revealedText, isAnimating }
}
