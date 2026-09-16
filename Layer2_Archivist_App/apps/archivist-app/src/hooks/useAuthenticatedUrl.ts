// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useEffect, useRef, useState } from 'react'
import { apiService } from '@/services/api'

/**
 * Fetches an asset URL through the authenticated asset-proxy and returns
 * a local blob object-URL that can be used in `src` attributes.
 *
 * Automatically revokes the previous object-URL on URL change and on unmount.
 */
export function useAuthenticatedUrl(rawUrl: string | undefined): string {
  const [blobUrl, setBlobUrl] = useState('')
  const blobUrlRef = useRef<string>('')

  useEffect(() => {
    if (!rawUrl) {
      setBlobUrl('')
      return
    }

    let cancelled = false

    apiService.fetchBlobObjectUrl(rawUrl).then((url) => {
      if (!cancelled) {
        // Revoke the previous blob URL
        if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current)
        blobUrlRef.current = url
        setBlobUrl(url)
      } else {
        URL.revokeObjectURL(url)
      }
    }).catch(() => {
      if (!cancelled) setBlobUrl('')
    })

    return () => {
      cancelled = true
    }
  }, [rawUrl])

  // Revoke on unmount
  useEffect(() => {
    return () => {
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current)
    }
  }, [])

  return blobUrl
}
