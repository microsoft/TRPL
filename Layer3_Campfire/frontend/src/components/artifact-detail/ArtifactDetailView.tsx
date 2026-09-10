'use client'

import { useEffect, useRef } from 'react'
import { useParams, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'
import { InlineIcon } from '@/components/ui'
import { useArtifactDetail } from '@/hooks/useArtifactDetail'
import { useDetailViewAnimation } from '@/hooks/useDetailViewAnimation'
import { LetterDetail } from './LetterDetail'
import { BookDetail } from './BookDetail'
import styles from './ArtifactDetailView.module.css'

export function ArtifactDetailView() {
  const { containerRef, headerRef, contentRef, backgroundOverlayRef, triggerLeaveAnimation } =
    useDetailViewAnimation()

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [])

  const params = useParams()
  const searchParams = useSearchParams()

  const id = params.id as string
  const source = searchParams.get('source') || 'letter'
  const from = searchParams.get('from')
  const destination = from === 'chat' ? '/chat' : '/'

  const { data: artifact, isLoading: loading, error } = useArtifactDetail(id, source)

  const hasRedirected = useRef(false)
  useEffect(() => {
    if (!error || hasRedirected.current) return
    hasRedirected.current = true

    toast.error('Unable to load artifact', {
      description: 'The artifact could not be found or is temporarily unavailable.',
    })
    triggerLeaveAnimation(destination)
  }, [error, destination, triggerLeaveAnimation])

  return (
    <main ref={containerRef} className={styles.page}>
      <header ref={headerRef} className={styles.header}>
        <button
          className={styles.backButton}
          onClick={() => triggerLeaveAnimation(destination)}
          aria-label="Go back"
        >
          <InlineIcon name="arrow-left" size={40} />
          <span className={styles.backLabel}>{from === 'chat' ? 'Back to Chat' : 'Back to Homepage'}</span>
        </button>
      </header>

      <div ref={contentRef} className={styles.content}>
        {loading && <p className={styles.placeholder}>Loading artifact...</p>}
        {!loading && !error && artifact && (
          source === 'book'
            ? <BookDetail data={artifact} />
            : <LetterDetail data={artifact} />
        )}
      </div>

      <div ref={backgroundOverlayRef} className={styles.backgroundOverlay} />
    </main>
  )
}
