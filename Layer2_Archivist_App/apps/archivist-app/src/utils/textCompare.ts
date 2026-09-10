/**
 * Trimmed, case-insensitive string equality for filters (repository/collection labels, etc.).
 */
export function equalsIgnoreCase(
  a: string | null | undefined,
  b: string | null | undefined
): boolean {
  if (a == null && b == null) return true
  if (a == null || b == null) return false
  return a.trim().toLowerCase() === b.trim().toLowerCase()
}
