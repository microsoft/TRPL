/**
 * Dependency-free correction intake caller (dev).
 *
 * Uses Node's built-in fetch (Node 18+).
 *
 * Env vars:
 * - API_BASE_URL (required) e.g. https://<archivist-api-host>
 * - ACCESS_TOKEN (required) Bearer token for CORRECTION_INTAKE_AUDIENCE
 * - RECORD_ID (optional) UUID to submit
 * - NOTE (optional) note text
 * - SOURCE (optional) label (display-only)
 */
const apiBase = process.env.API_BASE_URL
const token = process.env.ACCESS_TOKEN

if (!apiBase) throw new Error('API_BASE_URL is required')
if (!token) throw new Error('ACCESS_TOKEN is required')

const recordId =
  (process.env.RECORD_ID && process.env.RECORD_ID.trim()) ||
  '00000000-0000-0000-0000-000000000000'
const note =
  (process.env.NOTE && process.env.NOTE.trim()) ||
  'Test correction note from correction-intake-test-caller'
const source = (process.env.SOURCE && process.env.SOURCE.trim()) || 'DummyCaller'

const url = `${apiBase.replace(/\/$/, '')}/api/v1/correction-requests`

const res = await fetch(url, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  },
  body: JSON.stringify({
    record_id: recordId,
    note,
    source,
  }),
})

const text = await res.text()
// Print status + body for debugging in CI/terminal.
console.log(`status: ${res.status}`)
console.log(text)

if (!res.ok) process.exit(1)
