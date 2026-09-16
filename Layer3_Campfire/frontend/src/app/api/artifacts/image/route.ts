// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { NextRequest, NextResponse } from 'next/server'
import sharp from 'sharp'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'

// Hard cap on a single image response. Any upstream Content-Length above this
// is rejected before streaming starts, so a hostile or accidental large blob
// cannot OOM the runtime. Images are smaller than PDFs, so the cap is tighter.
const MAX_IMAGE_BYTES = 25 * 1024 * 1024

function isTiff(contentType: string, url: string): boolean {
  if (contentType.includes('tiff')) return true
  const path = url.split('?')[0].toLowerCase()
  return path.endsWith('.tif') || path.endsWith('.tiff')
}

/**
 * GET /api/artifacts/image?url=<encoded-blob-url>
 *
 * Streams artifact images from the project's Azure storage account to the
 * browser. The host must match `${AZURE_STORAGE_ACCOUNT}.blob.core.windows.net`
 * exactly — any other host (including other Azure storage accounts) is
 * refused, which blocks SSRF via the `url` parameter.
 *
 * Mirrors the PDF proxy (`/api/artifacts/pdf`). The reason both exist is that
 * under Data Foundations zero-trust the public blob endpoint is disabled, so
 * blobs can only be fetched server-side from inside the peered VNet. The
 * browser cannot reach them directly.
 */
export async function GET(request: NextRequest) {
  const account = env.AZURE_STORAGE_ACCOUNT
  if (!account) {
    logger.error('Image proxy disabled: AZURE_STORAGE_ACCOUNT not configured')
    return NextResponse.json(
      { error: 'Image proxy not configured' },
      { status: 503 },
    )
  }

  const url = request.nextUrl.searchParams.get('url')
  if (!url) {
    return NextResponse.json({ error: 'url parameter is required' }, { status: 400 })
  }

  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return NextResponse.json({ error: 'Invalid URL' }, { status: 400 })
  }

  const expectedHost = `${account}.blob.core.windows.net`
  if (parsed.protocol !== 'https:' || parsed.hostname !== expectedHost) {
    return NextResponse.json({ error: 'URL not allowed' }, { status: 403 })
  }

  try {
    const upstreamHeaders: Record<string, string> = {}
    const range = request.headers.get('range')
    if (range) upstreamHeaders.Range = range

    const response = await fetch(url, { headers: upstreamHeaders })

    if (!response.ok && response.status !== 206) {
      logger.error('Image proxy fetch failed', { status: response.status })
      return NextResponse.json(
        { error: 'Failed to fetch image' },
        { status: response.status },
      )
    }

    const contentLengthHeader = response.headers.get('content-length')
    const contentLength = contentLengthHeader ? Number(contentLengthHeader) : NaN
    if (Number.isFinite(contentLength) && contentLength > MAX_IMAGE_BYTES) {
      logger.error('Image proxy rejected oversized response', {
        contentLength,
        max: MAX_IMAGE_BYTES,
      })
      return NextResponse.json({ error: 'Image too large' }, { status: 413 })
    }

    if (!response.body) {
      return NextResponse.json({ error: 'Empty response from upstream' }, { status: 502 })
    }

    const contentType = response.headers.get('content-type') || ''

    // TIFF images cannot be rendered by browsers natively — transcode to WebP.
    if (isTiff(contentType, url)) {
      const buffer = Buffer.from(await response.arrayBuffer())
      try {
        const webp = await sharp(buffer).webp().toBuffer()
        return new NextResponse(new Uint8Array(webp), {
          status: 200,
          headers: {
            'Content-Type': 'image/webp',
            'Cache-Control': 'private, max-age=3600',
            'Content-Length': String(webp.byteLength),
          },
        })
      } catch (transcodeError) {
        logger.error('TIFF transcode failed', {
          error: transcodeError instanceof Error ? transcodeError.message : 'Unknown',
        })
        return NextResponse.json({ error: 'Failed to transcode image' }, { status: 502 })
      }
    }

    // Pass through the upstream Content-Type rather than hardcoding one — blobs
    // may be jpeg, png, tiff, etc. Default to a generic binary type if absent.
    const responseHeaders: Record<string, string> = {
      'Content-Type': contentType || 'application/octet-stream',
      'Cache-Control': 'private, max-age=3600',
    }
    if (contentLengthHeader) responseHeaders['Content-Length'] = contentLengthHeader
    const contentRange = response.headers.get('content-range')
    if (contentRange) responseHeaders['Content-Range'] = contentRange
    const acceptRanges = response.headers.get('accept-ranges')
    if (acceptRanges) responseHeaders['Accept-Ranges'] = acceptRanges

    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    })
  } catch (error) {
    logger.error('Image proxy error', {
      error: error instanceof Error ? error.message : 'Unknown',
    })
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
