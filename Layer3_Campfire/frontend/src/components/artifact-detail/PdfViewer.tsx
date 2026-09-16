// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { loadPdfJs } from '@/lib/pdf'
import styles from './PdfViewer.module.css'

interface PdfViewerProps {
  url: string
  pageIndex: number
  onPageCountChange: (count: number) => void
}

export function PdfViewer({ url, pageIndex, onPageCountChange }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [pdfDoc, setPdfDoc] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const renderTaskRef = useRef<any>(null)

  // Load the PDF document
  useEffect(() => {
    let cancelled = false

    async function loadPdf() {
      const pdfjs = await loadPdfJs()
      const proxyUrl = `/api/artifacts/pdf?url=${encodeURIComponent(url)}`
      const doc = await pdfjs.getDocument(proxyUrl).promise
      if (cancelled) {
        doc.destroy()
        return
      }
      setPdfDoc(doc)
      onPageCountChange(doc.numPages)
      setLoading(false)
    }

    setLoading(true)
    loadPdf()

    return () => {
      cancelled = true
    }
  }, [url, onPageCountChange])

  // Render the current page
  const renderPage = useCallback(async () => {
    if (!pdfDoc || !canvasRef.current) return

    // Cancel any in-progress render
    if (renderTaskRef.current) {
      renderTaskRef.current.cancel()
      renderTaskRef.current = null
    }

    const page = await pdfDoc.getPage(pageIndex + 1) // pdfjs uses 1-based pages
    const dpr = window.devicePixelRatio || 1
    const viewport = page.getViewport({ scale: dpr })
    const canvas = canvasRef.current

    canvas.width = viewport.width
    canvas.height = viewport.height
    canvas.style.aspectRatio = `${viewport.width} / ${viewport.height}`

    const task = page.render({ canvas, viewport })
    renderTaskRef.current = task

    try {
      await task.promise
    } catch {
      // Render was cancelled — safe to ignore
    }
  }, [pdfDoc, pageIndex])

  useEffect(() => {
    renderPage()
  }, [renderPage])

  useEffect(() => {
    return () => {
      renderTaskRef.current?.cancel()
    }
  }, [])

  useEffect(() => {
    if (!pdfDoc) return
    return () => {
      pdfDoc.destroy()
    }
  }, [pdfDoc])

  if (loading) {
    return <div className={styles.loading}>Loading PDF…</div>
  }

  return (
    <div className={styles.wrapper}>
      <canvas ref={canvasRef} className={styles.canvas} />
    </div>
  )
}
