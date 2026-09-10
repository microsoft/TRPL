import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const PORTAL_ROOT = typeof document !== 'undefined' ? document.body : null

/**
 * Renders children on document.body so fixed overlays sit above Layout Header (z-50)
 * and escape the main column stacking context (z-0).
 * Locks layout scroll while mounted.
 */
export default function ModalPortal({ children }: { children: ReactNode }) {
  useEffect(() => {
    const html = document.documentElement
    const body = document.body
    const main = document.querySelector('main')
    const prevHtml = html.style.overflow
    const prevBody = body.style.overflow
    const prevMain = main instanceof HTMLElement ? main.style.overflow : ''
    html.style.overflow = 'hidden'
    body.style.overflow = 'hidden'
    if (main instanceof HTMLElement) main.style.overflow = 'hidden'
    return () => {
      html.style.overflow = prevHtml
      body.style.overflow = prevBody
      if (main instanceof HTMLElement) main.style.overflow = prevMain
    }
  }, [])

  if (!PORTAL_ROOT) return null
  return createPortal(children, PORTAL_ROOT)
}
