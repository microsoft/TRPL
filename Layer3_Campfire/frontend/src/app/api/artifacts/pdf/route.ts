import { NextRequest, NextResponse } from 'next/server'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'

// Hard cap on a single PDF response. Any upstream Content-Length above this
// is rejected before streaming starts, so a hostile or accidental large blob
// cannot OOM the runtime.
const MAX_PDF_BYTES = 50 * 1024 * 1024

/**
 * GET /api/artifacts/pdf?url=<encoded-blob-url>
 *
 * Streams PDF responses from the project's Azure storage account to the
 * browser. The host must match `${AZURE_STORAGE_ACCOUNT}.blob.core.windows.net`
 * exactly — any other host (including other Azure storage accounts) is
 * refused, which blocks SSRF via the `url` parameter.
 */
export async function GET(request: NextRequest) {
  const account = env.AZURE_STORAGE_ACCOUNT
  if (!account) {
    logger.error('PDF proxy disabled: AZURE_STORAGE_ACCOUNT not configured')
    return NextResponse.json(
      { error: 'PDF proxy not configured' },
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
      logger.error('PDF proxy fetch failed', { status: response.status })
      return NextResponse.json(
        { error: 'Failed to fetch PDF' },
        { status: response.status },
      )
    }

    const contentLengthHeader = response.headers.get('content-length')
    const contentLength = contentLengthHeader ? Number(contentLengthHeader) : NaN
    if (Number.isFinite(contentLength) && contentLength > MAX_PDF_BYTES) {
      logger.error('PDF proxy rejected oversized response', {
        contentLength,
        max: MAX_PDF_BYTES,
      })
      return NextResponse.json({ error: 'PDF too large' }, { status: 413 })
    }

    if (!response.body) {
      return NextResponse.json({ error: 'Empty response from upstream' }, { status: 502 })
    }

    const responseHeaders: Record<string, string> = {
      'Content-Type': 'application/pdf',
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
    logger.error('PDF proxy error', {
      error: error instanceof Error ? error.message : 'Unknown',
    })
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
