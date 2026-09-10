/**
 * In-memory token-bucket rate limiter.
 *
 * State lives in a module-scope Map and persists across requests within
 * a single Node.js process. Resets on deploy / restart. Not synced across
 * App Service instances — see plan in current-state-no-virtual-wilkes.md.
 */

export type RateLimitOptions = {
  capacity: number
  refillPerSecond: number
}

export type RateLimitResult = {
  allowed: boolean
  remaining: number
  resetAt: number
  retryAfterSeconds: number
}

type Bucket = {
  tokens: number
  lastRefill: number
}

const buckets = new Map<string, Bucket>()

// Sweep stale entries every CLEANUP_INTERVAL calls so the Map can't grow unbounded.
const CLEANUP_INTERVAL = 1000
const STALE_AFTER_MS = 60 * 60 * 1000
let callsSinceCleanup = 0

function sweepStale(now: number): void {
  for (const [key, bucket] of buckets) {
    if (now - bucket.lastRefill > STALE_AFTER_MS) {
      buckets.delete(key)
    }
  }
}

export function checkLimit(key: string, opts: RateLimitOptions): RateLimitResult {
  const now = Date.now()

  callsSinceCleanup++
  if (callsSinceCleanup >= CLEANUP_INTERVAL) {
    callsSinceCleanup = 0
    sweepStale(now)
  }

  let bucket = buckets.get(key)
  if (!bucket) {
    bucket = { tokens: opts.capacity, lastRefill: now }
    buckets.set(key, bucket)
  } else {
    const elapsedSeconds = (now - bucket.lastRefill) / 1000
    const refilled = elapsedSeconds * opts.refillPerSecond
    bucket.tokens = Math.min(opts.capacity, bucket.tokens + refilled)
    bucket.lastRefill = now
  }

  if (bucket.tokens >= 1) {
    bucket.tokens -= 1
    const remaining = Math.floor(bucket.tokens)
    const tokensToFull = opts.capacity - bucket.tokens
    const resetAt = now + (tokensToFull / opts.refillPerSecond) * 1000
    return { allowed: true, remaining, resetAt, retryAfterSeconds: 0 }
  }

  const tokensNeeded = 1 - bucket.tokens
  const retryAfterSeconds = Math.ceil(tokensNeeded / opts.refillPerSecond)
  const resetAt = now + retryAfterSeconds * 1000
  return { allowed: false, remaining: 0, resetAt, retryAfterSeconds }
}

/** Test-only: clear all buckets so tests don't bleed state. */
export function __resetForTests(): void {
  buckets.clear()
  callsSinceCleanup = 0
}

/** Test-only: inspect bucket count to verify cleanup behavior. */
export function __bucketCountForTests(): number {
  return buckets.size
}
