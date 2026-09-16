// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * True if metadata "Date Published to Portal" is present and non-empty.
 * Matches backend normalization (string, { label }, arrays, numbers).
 */
export function hasPortalPublishDateMetadata(value: unknown): boolean {
  if (value === null || value === undefined) {
    return false
  }
  if (typeof value === 'string') {
    return value.trim().length > 0
  }
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return true
  }
  if (typeof value === 'object' && !Array.isArray(value)) {
    const o = value as Record<string, unknown>
    const label = o.label ?? o.value ?? o.name ?? o.text
    if (label !== undefined && label !== null) {
      return String(label).trim().length > 0
    }
    return false
  }
  if (Array.isArray(value)) {
    const joined = value
      .map((item) => (item === null || item === undefined ? '' : String(item)))
      .filter(Boolean)
      .join(', ')
    return joined.trim().length > 0
  }
  return String(value).trim().length > 0
}
