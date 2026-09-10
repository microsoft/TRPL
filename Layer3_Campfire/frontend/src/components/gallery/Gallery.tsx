'use client'

import { useEffect, useState } from 'react'
import { VerticalGallery } from './VerticalGallery'
import { HorizontalGallery } from './HorizontalGallery'
import styles from './Gallery.module.css'
import type { GalleryImage } from './VerticalGallery'
import { GALLERY } from '@/lib/constants'

interface GalleryProps {
  images: GalleryImage[]
  scrollSpeed?: number
  className?: string
  columns?: 1 | 2
  autoScroll?: boolean
  showActions?: boolean
  onImageAction?: (image: GalleryImage) => void
}

/**
 * Gallery - Responsive gallery component that switches between layouts
 *
 * Uses a media query listener to render only one gallery variant at a time.
 *
 * - Desktop (>= 1200px): VerticalGallery with two-column vertical scroll, hover-to-select
 * - Mobile/Tablet (< 1200px): HorizontalGallery with two-row horizontal scroll, click-to-select
 *
 * @param props - Gallery configuration props
 * @param props.images - Array of images to display
 * @param props.scrollSpeed - Pixels per second for auto-scroll (default: 30)
 * @param props.className - Optional additional CSS classes
 */
export const Gallery = ({
  images,
  scrollSpeed = 30,
  className,
  columns = 2,
  autoScroll = true,
  showActions = false,
  onImageAction,
}: GalleryProps) => {
  const [isDesktop, setIsDesktop] = useState<boolean | null>(null)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      setIsDesktop(true)
      return
    }

    const mediaQuery = window.matchMedia(`(min-width: ${GALLERY.DESKTOP_BREAKPOINT}px)`)
    const updateVariant = (event?: MediaQueryListEvent) => {
      setIsDesktop(event ? event.matches : mediaQuery.matches)
    }

    updateVariant()

    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', updateVariant)
      return () => mediaQuery.removeEventListener('change', updateVariant)
    }

    mediaQuery.addListener(updateVariant)
    return () => mediaQuery.removeListener(updateVariant)
  }, [])

  if (isDesktop === null) {
    return (
      <div className={styles.placeholder} aria-hidden="true" />
    )
  }

  if (isDesktop) {
    return (
      <VerticalGallery
        images={images}
        scrollSpeed={scrollSpeed}
        className={className}
        columns={columns}
        autoScroll={autoScroll}
        showActions={showActions}
        onImageAction={onImageAction}
      />
    )
  }

  return (
    <HorizontalGallery
      images={images}
      scrollSpeed={scrollSpeed}
      className={className}
      onImageAction={onImageAction}
    />
  )
}
