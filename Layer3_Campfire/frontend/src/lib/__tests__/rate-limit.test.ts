// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { checkLimit, __resetForTests, __bucketCountForTests } from '../rate-limit'

describe('checkLimit', () => {
  beforeEach(() => {
    __resetForTests()
    jest.useFakeTimers()
    jest.setSystemTime(new Date('2026-01-01T00:00:00Z'))
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('allows requests up to capacity in a single burst', () => {
    const opts = { capacity: 5, refillPerSecond: 1 }
    for (let i = 0; i < 5; i++) {
      expect(checkLimit('ip1', opts).allowed).toBe(true)
    }
    expect(checkLimit('ip1', opts).allowed).toBe(false)
  })

  it('returns descending remaining within a burst', () => {
    const opts = { capacity: 5, refillPerSecond: 1 }
    expect(checkLimit('ip1', opts).remaining).toBe(4)
    expect(checkLimit('ip1', opts).remaining).toBe(3)
    expect(checkLimit('ip1', opts).remaining).toBe(2)
  })

  it('refills tokens over time', () => {
    const opts = { capacity: 5, refillPerSecond: 1 }
    for (let i = 0; i < 5; i++) checkLimit('ip1', opts)
    expect(checkLimit('ip1', opts).allowed).toBe(false)

    jest.advanceTimersByTime(2000)
    expect(checkLimit('ip1', opts).allowed).toBe(true)
    expect(checkLimit('ip1', opts).allowed).toBe(true)
    expect(checkLimit('ip1', opts).allowed).toBe(false)
  })

  it('caps refill at capacity', () => {
    const opts = { capacity: 3, refillPerSecond: 10 }
    for (let i = 0; i < 3; i++) checkLimit('ip1', opts)
    expect(checkLimit('ip1', opts).allowed).toBe(false)

    jest.advanceTimersByTime(60_000)
    for (let i = 0; i < 3; i++) {
      expect(checkLimit('ip1', opts).allowed).toBe(true)
    }
    expect(checkLimit('ip1', opts).allowed).toBe(false)
  })

  it('isolates buckets by key', () => {
    const opts = { capacity: 2, refillPerSecond: 1 }
    expect(checkLimit('ip1', opts).allowed).toBe(true)
    expect(checkLimit('ip1', opts).allowed).toBe(true)
    expect(checkLimit('ip1', opts).allowed).toBe(false)

    expect(checkLimit('ip2', opts).allowed).toBe(true)
    expect(checkLimit('ip2', opts).allowed).toBe(true)
    expect(checkLimit('ip2', opts).allowed).toBe(false)
  })

  it('reports a retry-after when rejecting', () => {
    const opts = { capacity: 1, refillPerSecond: 0.5 }
    expect(checkLimit('ip1', opts).allowed).toBe(true)
    const denied = checkLimit('ip1', opts)
    expect(denied.allowed).toBe(false)
    expect(denied.retryAfterSeconds).toBe(2)
  })

  it('reports resetAt = now + retryAfter*1000 when denied', () => {
    const opts = { capacity: 1, refillPerSecond: 0.5 }
    checkLimit('ip1', opts)
    const denied = checkLimit('ip1', opts)
    expect(denied.allowed).toBe(false)
    expect(denied.retryAfterSeconds).toBe(2)
    expect(denied.resetAt).toBe(Date.now() + 2000)
  })

  it('reports resetAt = time until bucket is full when allowed', () => {
    const opts = { capacity: 4, refillPerSecond: 1 }
    const first = checkLimit('ip1', opts)
    expect(first.allowed).toBe(true)
    // 3 tokens left, refilling at 1/s → 1s to full
    expect(first.resetAt).toBeGreaterThanOrEqual(Date.now() + 1000 - 50)
    expect(first.resetAt).toBeLessThanOrEqual(Date.now() + 1000 + 50)
  })

  it('partially refills when called with sub-second elapsed time', () => {
    const opts = { capacity: 2, refillPerSecond: 1 }
    expect(checkLimit('ip1', opts).allowed).toBe(true)
    expect(checkLimit('ip1', opts).allowed).toBe(true)
    expect(checkLimit('ip1', opts).allowed).toBe(false)

    // Half a second: only 0.5 tokens refilled, still not enough for one request
    jest.advanceTimersByTime(500)
    expect(checkLimit('ip1', opts).allowed).toBe(false)

    // Another half second: now we have 1 token
    jest.advanceTimersByTime(500)
    expect(checkLimit('ip1', opts).allowed).toBe(true)
  })

  it('sweeps stale buckets after the cleanup interval', () => {
    const opts = { capacity: 1, refillPerSecond: 1 }
    checkLimit('stale-ip', opts)
    expect(__bucketCountForTests()).toBe(1)

    // Jump past the stale threshold without touching 'stale-ip'.
    jest.advanceTimersByTime(2 * 60 * 60 * 1000)

    // Trigger cleanup by hitting the call threshold with a different key.
    for (let i = 0; i < 1000; i++) {
      checkLimit('active-ip', opts)
    }

    expect(__bucketCountForTests()).toBe(1)
  })
})
