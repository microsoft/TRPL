import { NextRequest, NextResponse } from 'next/server'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'

/**
 * POST /api/artifacts
 *
 * Proxies artifact requests to the Python RAG backend.
 * Expects JSON body: { index: "letter" | "book", id: string }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { index, id } = body

    if (!index || !id) {
      return NextResponse.json(
        { error: 'index and id are required' },
        { status: 400 }
      )
    }

    const backendUrl = new URL('/api/artifacts', env.PYTHON_RAG_API_URL)

    const response = await fetch(backendUrl.toString(), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${env.RAG_API_KEY}`,
      },
      body: JSON.stringify({ index, id }),
    })

    if (!response.ok) {
      const errorText = await response.text()
      logger.error('Backend artifact fetch failed', { status: response.status, error: errorText })
      return NextResponse.json(
        { error: errorText || 'Failed to fetch artifact' },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    logger.error('Artifact API error', {
      error: error instanceof Error ? error.message : 'Unknown',
    })
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
