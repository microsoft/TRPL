// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useEffect, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import { useAuthenticatedUrl } from '@/hooks/useAuthenticatedUrl'

pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.mjs`

type SinglePagePdfViewerProps = {
  fileUrl: string
  pageNumber: number
  className?: string
  scale?: number
}

const isLikelyCorsBlockedUrl = (url: string) =>
  url.includes('amazonaws.com') || url.includes('theodorerooseveltcenter.org')

const SinglePagePdfViewer: React.FC<SinglePagePdfViewerProps> = ({
  fileUrl,
  pageNumber,
  className = '',
  scale = 1,
}) => {
  const [numPages, setNumPages] = useState<number | null>(null)
  const [useIframe, setUseIframe] = useState(isLikelyCorsBlockedUrl(fileUrl))
  const authenticatedUrl = useAuthenticatedUrl(fileUrl)

  useEffect(() => {
    setNumPages(null)
    setUseIframe(isLikelyCorsBlockedUrl(fileUrl))
  }, [fileUrl])

  const safePage = Math.max(pageNumber, 1)

  if (useIframe) {
    const iframeSrc = authenticatedUrl || fileUrl
    return (
      <div className={`h-full w-full bg-gray-100 ${className}`}>
        <iframe
          key={`${iframeSrc}-${safePage}`}
          src={`${iframeSrc}#page=${safePage}`}
          title={`PDF page ${safePage}`}
          className="w-full h-full min-h-[640px] border-0 bg-white"
        />
      </div>
    )
  }

  if (!authenticatedUrl) {
    return (
      <div className={`flex items-start justify-center bg-gray-100 overflow-auto h-full ${className}`}>
        <div className="text-gray-500 p-8">Loading PDF...</div>
      </div>
    )
  }

  const boundedPage = numPages ? Math.min(safePage, numPages) : safePage

  return (
    <div className={`flex items-start justify-center bg-gray-100 overflow-auto h-full ${className}`}>
      <Document
        file={authenticatedUrl}
        onLoadSuccess={({ numPages: total }) => setNumPages(total)}
        onLoadError={() => setUseIframe(true)}
        loading={<div className="text-gray-500 p-8">Loading PDF...</div>}
        error={<div className="text-red-600 p-8">Unable to load PDF.</div>}
      >
        <Page
          key={`${authenticatedUrl}-${boundedPage}`}
          pageNumber={boundedPage}
          width={640 * scale}
          renderTextLayer={false}
          renderAnnotationLayer={false}
          loading={<div className="text-gray-500 p-8">Loading page...</div>}
        />
      </Document>
    </div>
  )
}

export default SinglePagePdfViewer
