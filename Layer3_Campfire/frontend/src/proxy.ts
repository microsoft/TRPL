import { NextRequest, NextResponse } from 'next/server'
import { checkLimit, type RateLimitOptions } from '@/lib/rate-limit'

export const config = {
  matcher: ['/((?!_next/static|_next/image).*)'],
}

const CHAT_BODY_LIMIT_BYTES = 10 * 1024
const DEFAULT_API_BODY_LIMIT_BYTES = 100 * 1024

const CHAT_LIMIT: RateLimitOptions = { capacity: 5, refillPerSecond: 0.25 }
const API_LIMIT: RateLimitOptions = { capacity: 20, refillPerSecond: 1 }

// Number of reverse proxies between the public internet and this app.
// X-Forwarded-For is parsed from the right: the rightmost N entries are
// added by trusted proxies; the (N+1)th-from-the-right entry is the real
// client IP. Anything to the left is client-injected and must be ignored.
//
// Defaults to 1 (Azure App Service / Vercel direct). Set to 2 if behind
// Front Door + App Service, etc. Set to 0 to disable per-IP rate limiting
// (lumps every caller into one bucket — only safe for local dev).
const TRUSTED_PROXY_HOPS: number = (() => {
  const raw = Number(process.env.TRUSTED_PROXY_HOPS ?? '1')
  return Number.isFinite(raw) && raw >= 0 ? Math.floor(raw) : 1
})()

const SECURITY_HEADERS: Record<string, string> = {
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
}

// Turbopack/Webpack HMR in dev relies on eval(); production builds do not.
const IS_DEV = process.env.NODE_ENV !== 'production'

export function normalizeExternalImageOrigin(value: string | undefined): string | null {
  const raw = value?.trim()
  if (!raw) return null

  let url: URL
  try {
    url = new URL(raw)
  } catch {
    throw new Error('EXTERNAL_IMAGE_ORIGIN must be a valid HTTPS origin')
  }
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash
  ) {
    throw new Error('EXTERNAL_IMAGE_ORIGIN must be an HTTPS origin without credentials or a path')
  }
  return url.origin
}

const EXTERNAL_IMAGE_ORIGIN = normalizeExternalImageOrigin(process.env.EXTERNAL_IMAGE_ORIGIN)

const IMAGE_SRC = EXTERNAL_IMAGE_ORIGIN
  ? `img-src 'self' data: blob: https://*.blob.core.windows.net ${EXTERNAL_IMAGE_ORIGIN}`
  : "img-src 'self' data: blob: https://*.blob.core.windows.net"

const CSP = [
  "default-src 'self'",
  IS_DEV
    ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
    : "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  IMAGE_SRC,
  "connect-src 'self' https://*.in.applicationinsights.azure.com https://*.livediagnostics.monitor.azure.com https://dc.services.visualstudio.com",
  "font-src 'self' data:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join('; ')

function clientIp(request: NextRequest): string {
  if (TRUSTED_PROXY_HOPS === 0) return 'unknown'
  const forwarded = request.headers.get('x-forwarded-for')
  if (!forwarded) return 'unknown'
  const parts = forwarded.split(',').map((s) => s.trim()).filter(Boolean)
  // If fewer entries exist than trusted hops, the request didn't come through
  // the expected proxy chain — every visible entry could be client-injected.
  if (parts.length < TRUSTED_PROXY_HOPS) return 'unknown'
  return parts[parts.length - TRUSTED_PROXY_HOPS]
}

function applySecurityHeaders(response: NextResponse): NextResponse {
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    response.headers.set(name, value)
  }
  response.headers.set('Content-Security-Policy', CSP)
  return response
}

function jsonResponse(status: number, body: object, extraHeaders: Record<string, string> = {}): NextResponse {
  const response = NextResponse.json(body, { status })
  for (const [name, value] of Object.entries(extraHeaders)) {
    response.headers.set(name, value)
  }
  return applySecurityHeaders(response)
}

export function proxy(request: NextRequest): NextResponse {
  const pathname = request.nextUrl.pathname

  // Azure App Service liveness probe must always pass through.
  if (pathname === '/api/health') {
    return applySecurityHeaders(NextResponse.next())
  }

  const isApi = pathname.startsWith('/api/')
  const isChat = pathname === '/api/chat'

  if (isApi) {
    const method = request.method.toUpperCase()
    if (method === 'POST' || method === 'PUT' || method === 'PATCH') {
      // Transfer-Encoding (typically 'chunked') hides the true body size from
      // headers. Middleware can't consume the body to count bytes, so the
      // only safe move is to refuse such requests on size-capped endpoints.
      if (request.headers.get('transfer-encoding')) {
        return jsonResponse(413, { error: 'Chunked transfer encoding not supported' })
      }
      const limit = isChat ? CHAT_BODY_LIMIT_BYTES : DEFAULT_API_BODY_LIMIT_BYTES
      const raw = request.headers.get('content-length')
      // Previously a missing header defaulted to 0 and silently passed any
      // body through; require an honest declared length instead.
      if (raw === null) {
        return jsonResponse(411, { error: 'Content-Length required' })
      }
      const contentLength = Number(raw)
      if (!Number.isFinite(contentLength) || contentLength < 0) {
        return jsonResponse(400, { error: 'Invalid Content-Length' })
      }
      if (contentLength > limit) {
        return jsonResponse(413, { error: 'Request body too large' })
      }
    }

    const key = `${clientIp(request)}:${isChat ? 'chat' : 'api'}`
    const opts = isChat ? CHAT_LIMIT : API_LIMIT
    const result = checkLimit(key, opts)

    if (!result.allowed) {
      return jsonResponse(
        429,
        { error: 'Too many requests' },
        {
          'Retry-After': String(result.retryAfterSeconds),
          'X-RateLimit-Limit': String(opts.capacity),
          'X-RateLimit-Remaining': '0',
          'X-RateLimit-Reset': String(Math.floor(result.resetAt / 1000)),
        },
      )
    }

    const response = NextResponse.next()
    response.headers.set('X-RateLimit-Limit', String(opts.capacity))
    response.headers.set('X-RateLimit-Remaining', String(result.remaining))
    response.headers.set('X-RateLimit-Reset', String(Math.floor(result.resetAt / 1000)))
    return applySecurityHeaders(response)
  }

  return applySecurityHeaders(NextResponse.next())
}
