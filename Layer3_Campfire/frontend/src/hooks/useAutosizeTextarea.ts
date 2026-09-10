'use client'

import { useCallback, useEffect, useRef } from 'react'

interface AutosizeOptions {
  minRows?: number
  maxRows?: number
}

export function useAutosizeTextarea(
  value: string,
  { minRows = 1, maxRows = 6 }: AutosizeOptions = {}
) {
  const ref = useRef<HTMLTextAreaElement>(null)

  const resize = useCallback(() => {
    const el = ref.current
    if (!el || typeof window === 'undefined') return

    const styles = window.getComputedStyle(el)
    const paddingTop = parseFloat(styles.paddingTop) || 0
    const paddingBottom = parseFloat(styles.paddingBottom) || 0

    const parsedLineHeight = parseFloat(styles.lineHeight)
    const parsedFontSize = parseFloat(styles.fontSize)
    const lineHeight = Number.isFinite(parsedLineHeight)
      ? parsedLineHeight
      : (Number.isFinite(parsedFontSize) ? parsedFontSize * 1.3 : 20)

    const minHeight = lineHeight * minRows + paddingTop + paddingBottom
    const maxHeight = lineHeight * maxRows + paddingTop + paddingBottom

    el.style.height = 'auto'
    const targetHeight = Math.max(minHeight, Math.min(el.scrollHeight, maxHeight))
    el.style.height = `${targetHeight}px`
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }, [maxRows, minRows])

  useEffect(() => {
    resize()
  }, [resize, value])

  return { textareaRef: ref, resizeTextarea: resize }
}
