// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const ANON_USER_ID_KEY = 'reading-room-anon-user-id'
let cachedUserId: string | null = null

function generateAnonymousUserId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

export function getOrCreateAnonymousUserId(): string {
  if (cachedUserId) return cachedUserId

  if (typeof window === 'undefined') {
    cachedUserId = 'anonymous'
    return cachedUserId
  }

  try {
    const existing = localStorage.getItem(ANON_USER_ID_KEY)
    if (existing) {
      cachedUserId = existing
      return existing
    }

    const newId = generateAnonymousUserId()
    localStorage.setItem(ANON_USER_ID_KEY, newId)
    cachedUserId = newId
    return newId
  } catch {
    const fallback = generateAnonymousUserId()
    cachedUserId = fallback
    return fallback
  }
}
