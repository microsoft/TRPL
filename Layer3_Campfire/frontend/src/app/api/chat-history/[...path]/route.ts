import { NextRequest, NextResponse } from 'next/server'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'
import { resolveUserId, setUserIdCookie } from '@/lib/userIdCookie'

export const dynamic = 'force-dynamic'

type RouteContext = {
  params: Promise<{ path?: string[] }>
}

const buildBackendUrl = (segments: string[]) => {
  const safeSegments = segments.map((segment) => encodeURIComponent(segment))
  return new URL(`/api/chat-history/${safeSegments.join('/')}`, env.PYTHON_RAG_API_URL).toString()
}

const proxyToBackend = async (segments: string[], method: 'GET' | 'DELETE') => {
  const backendUrl = buildBackendUrl(segments)
  const response = await fetch(backendUrl, {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${env.RAG_API_KEY}`,
    },
  })

  const body = await response.text()
  return new NextResponse(body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('content-type') ?? 'application/json',
    },
  })
}

// The URL's first segment is treated as a hint; the signed cookie is the
// only thing the proxy actually scopes the backend call under. This closes
// IDOR via leaked user_id values (the attacker would need to forge the
// cookie signature, not just guess the id).
function scopeSegments(segments: string[], userId: string): string[] {
  if (segments.length === 0) return segments
  return [userId, ...segments.slice(1)]
}

export async function GET(request: NextRequest, { params }: RouteContext) {
  const { path } = await params
  const segments = path ?? []

  const { userId, isFresh } = resolveUserId(request)
  const scoped = scopeSegments(segments, userId)

  try {
    let response: NextResponse | null = null
    if (segments.length === 1) {
      response = await proxyToBackend(scoped, 'GET')
    } else if (segments.length === 3 && segments[2] === 'messages') {
      response = await proxyToBackend(scoped, 'GET')
    }

    if (response) {
      if (isFresh) setUserIdCookie(response, userId)
      return response
    }
  } catch (error) {
    logger.error('Chat history proxy failed', {
      error: error instanceof Error ? error.message : 'Unknown',
      path: segments.join('/'),
    })
    return NextResponse.json({ error: 'Failed to fetch chat history' }, { status: 500 })
  }

  return NextResponse.json({ error: 'Invalid chat history route' }, { status: 400 })
}

export async function DELETE(request: NextRequest, { params }: RouteContext) {
  const { path } = await params
  const segments = path ?? []

  if (segments.length !== 2) {
    return NextResponse.json({ error: 'Invalid chat history route' }, { status: 400 })
  }

  const { userId, isFresh } = resolveUserId(request)
  const scoped = scopeSegments(segments, userId)

  try {
    const response = await proxyToBackend(scoped, 'DELETE')
    if (isFresh) setUserIdCookie(response, userId)
    return response
  } catch (error) {
    logger.error('Chat history delete proxy failed', {
      error: error instanceof Error ? error.message : 'Unknown',
      path: segments.join('/'),
    })
    return NextResponse.json({ error: 'Failed to delete chat' }, { status: 500 })
  }
}
