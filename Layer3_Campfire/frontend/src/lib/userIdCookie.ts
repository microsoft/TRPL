// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { createHmac, randomUUID, timingSafeEqual } from 'node:crypto'
import type { NextRequest, NextResponse } from 'next/server'
import { env } from '@/lib/env'

const COOKIE_NAME = 'rr_uid'
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365

// HMAC-sign the user_id so a leaked id (URL, screenshot, referrer log) cannot
// be turned into a cookie by an attacker — they would also need to forge a
// signature keyed by RAG_API_KEY.
function sign(userId: string): string {
  const sig = createHmac('sha256', env.RAG_API_KEY).update(userId).digest('base64url')
  return `${userId}.${sig}`
}

function verify(value: string): string | null {
  const dot = value.lastIndexOf('.')
  if (dot < 1) return null
  const userId = value.slice(0, dot)
  const sig = value.slice(dot + 1)
  const expected = createHmac('sha256', env.RAG_API_KEY).update(userId).digest('base64url')
  const sigBuf = Buffer.from(sig)
  const expBuf = Buffer.from(expected)
  if (sigBuf.length !== expBuf.length) return null
  try {
    if (!timingSafeEqual(sigBuf, expBuf)) return null
  } catch {
    return null
  }
  return userId
}

export type ResolvedUserId = {
  /** The verified user_id to scope backend calls under. */
  userId: string
  /** True if a new id was generated and the cookie should be set on the response. */
  isFresh: boolean
}

function secureCookieEnabled(): boolean {
  const configured = process.env.COOKIE_SECURE
  if (configured === undefined) return process.env.NODE_ENV === 'production'

  switch (configured.trim().toLowerCase()) {
    case 'true':
      return true
    case 'false':
      return false
    default:
      throw new Error('COOKIE_SECURE must be either "true" or "false"')
  }
}

export function resolveUserId(request: NextRequest): ResolvedUserId {
  const raw = request.cookies.get(COOKIE_NAME)?.value
  if (raw) {
    const userId = verify(raw)
    if (userId) return { userId, isFresh: false }
  }
  return { userId: randomUUID(), isFresh: true }
}

export function setUserIdCookie(response: NextResponse, userId: string): void {
  response.cookies.set({
    name: COOKIE_NAME,
    value: sign(userId),
    httpOnly: true,
    sameSite: 'lax',
    secure: secureCookieEnabled(),
    path: '/',
    maxAge: ONE_YEAR_SECONDS,
  })
}
