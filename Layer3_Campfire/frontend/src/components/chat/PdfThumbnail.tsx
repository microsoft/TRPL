// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useEffect, useRef, useState } from 'react'
import { loadPdfJs } from '@/lib/pdf'

interface PdfThumbnailProps {
  url: string
  className?: string
}

export function PdfThumbnail({ url, className }: PdfThumbnailProps) {
  const [dataUrl, setDataUrl] = useState<string | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const docRef = useRef<any>(null)

  useEffect(() => {
    let cancelled = false

    async function render() {
      const pdfjs = await loadPdfJs()
      const proxyUrl = `/api/artifacts/pdf?url=${encodeURIComponent(url)}`
      const doc = await pdfjs.getDocument(proxyUrl).promise

      if (cancelled) {
        doc.destroy()
        return
      }

      docRef.current = doc
      const page = await doc.getPage(1)
      const viewport = page.getViewport({ scale: 1 })

      // Scale to thumbnail height of 60px (matches .sourceThumbnail)
      const scale = 60 / viewport.height
      const thumbViewport = page.getViewport({ scale })

      const canvas = document.createElement('canvas')
      canvas.width = thumbViewport.width
      canvas.height = thumbViewport.height

      await page.render({
        canvasContext: canvas.getContext('2d')!,
        viewport: thumbViewport,
      }).promise

      if (!cancelled) {
        setDataUrl(canvas.toDataURL())
      }
    }

    render().catch(() => {
      // PDF failed to load — leave thumbnail blank
    })

    return () => {
      cancelled = true
      if (docRef.current) {
        docRef.current.destroy()
        docRef.current = null
      }
    }
  }, [url])

  if (!dataUrl) {
    return <div className={className} style={{ width: 42, height: 60, backgroundColor: 'rgba(0,0,0,0.05)', borderRadius: 2, flexShrink: 0 }} />
  }

  return <img src={dataUrl} alt="" className={className} />
}
