// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { ExternalLink, History, Layers, Library, Loader2, Search, X, FileWarning } from 'lucide-react'
import { apiService } from '@/services/api'

export interface PublishBatchHistoryModalProps {
  isOpen: boolean
  onClose: () => void
  canEdit: boolean
  onToast: (type: 'success' | 'error' | 'warning' | 'info', message: string) => void
  /** Called after an unpublish job is successfully started so the parent can refresh job status / scroll to Unpublish. */
  onUnpublishJobStarted?: () => void
}

type BatchRow = Record<string, unknown>

function countDocs(b: BatchRow): number {
  return Array.isArray(b.document_ids) ? b.document_ids.length : 0
}

function countSuccessful(b: BatchRow): number {
  return Array.isArray(b.successful_record_ids) ? b.successful_record_ids.length : 0
}

function rowBatchId(b: BatchRow): string {
  return String(b.id ?? b.batch_id ?? '')
}

function hasUnpublishTargets(b: BatchRow): boolean {
  return countSuccessful(b) > 0 || countDocs(b) > 0
}

/** Batch unpublish was started from this UI (or preserved in Cosmos) — never show Unpublish again. */
function hasBatchUnpublishLocked(b: BatchRow): boolean {
  const raw = b.unpublish_requested_at
  if (raw == null || raw === '') return false
  if (typeof raw === 'string') return raw.trim().length > 0
  return Boolean(raw)
}

function batchRepository(d: BatchRow): string {
  const v = d.repository
  if (v != null && String(v).trim() !== '') return String(v).trim()
  const details = d.successful_record_details
  if (Array.isArray(details) && details.length > 0) {
    const first = details[0] as Record<string, unknown>
    const r = first?.repository
    if (r != null && String(r).trim() !== '') return String(r).trim()
  }
  return ''
}

function batchCollection(d: BatchRow): string {
  const v = d.collection
  if (v != null && String(v).trim() !== '') return String(v).trim()
  const details = d.successful_record_details
  if (Array.isArray(details) && details.length > 0) {
    const first = details[0] as Record<string, unknown>
    const c = first?.collection
    if (c != null && String(c).trim() !== '') return String(c).trim()
  }
  return ''
}

/** Slim rows { record_id, title }; legacy entries used document_id and per-row repository/collection. */
function successfulRowsForDisplay(d: BatchRow): { recordId: string; title: string }[] {
  const details = d.successful_record_details
  if (Array.isArray(details) && details.length > 0) {
    return details.map((row, i) => {
      const o = row as Record<string, unknown>
      const recordId = String(o.record_id ?? o.document_id ?? i)
      const title = String(o.title ?? '')
      return { recordId, title }
    })
  }
  const ids = d.successful_record_ids
  if (Array.isArray(ids) && ids.length > 0) {
    return ids.map((id) => ({ recordId: String(id), title: '' }))
  }
  return []
}

function failedCount(detail: BatchRow): number {
  const f = detail.failed_records
  return Array.isArray(f) ? f.length : 0
}

/** Renders on document.body so z-index competes with Header (z-50); in-Outlet modals were trapped under Layout main (z-0). */
const PORTAL_ROOT = typeof document !== 'undefined' ? document.body : null

const PUBLISH_HISTORY_DIALOG_MAX_STYLE = {
  maxHeight: 'min(56rem, calc(100dvh - 2rem - env(safe-area-inset-top, 0px) - env(safe-area-inset-bottom, 0px)))',
} as const

export default function PublishBatchHistoryModal({
  isOpen,
  onClose,
  canEdit,
  onToast,
  onUnpublishJobStarted,
}: PublishBatchHistoryModalProps) {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [batches, setBatches] = useState<BatchRow[]>([])
  const [searchId, setSearchId] = useState('')
  /** When set, the table reflects a user search (including zero matches); do not replace with "recent" on parent re-renders. */
  const [activeSearchQuery, setActiveSearchQuery] = useState<string | null>(null)
  const [detail, setDetail] = useState<BatchRow | null>(null)
  const [pendingUnpublishBatchId, setPendingUnpublishBatchId] = useState<string | null>(null)
  const [isUnpublishing, setIsUnpublishing] = useState(false)

  const detailSuccessRows = useMemo(() => {
    if (!detail) return []
    const rows = successfulRowsForDisplay(detail)
    const q = activeSearchQuery?.trim()
    if (!q) return rows
    const needle = q.toLowerCase()
    const isMatch = (r: { recordId: string }) => r.recordId.trim().toLowerCase() === needle
    const matched = rows.filter(isMatch)
    if (matched.length === 0) return rows
    const rest = rows.filter((r) => !isMatch(r))
    return [...matched, ...rest]
  }, [detail, activeSearchQuery])
  const detailRepo = useMemo(() => (detail ? batchRepository(detail) : ''), [detail])
  const detailColl = useMemo(() => (detail ? batchCollection(detail) : ''), [detail])
  const detailFailedN = useMemo(() => (detail ? failedCount(detail) : 0), [detail])

  const goToRecord = useCallback(
    (recordId: string) => {
      const id = recordId.trim()
      if (!id) return
      onClose()
      navigate(`/review/${encodeURIComponent(id)}`)
    },
    [navigate, onClose]
  )

  const onToastRef = useRef(onToast)
  const onUnpublishJobStartedRef = useRef(onUnpublishJobStarted)
  const modalOverlayRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    onToastRef.current = onToast
    onUnpublishJobStartedRef.current = onUnpublishJobStarted
  }, [onToast, onUnpublishJobStarted])

  /** Reset overlay scroll when opening, when leaving batch detail (re-centre), and when opening detail (top-aligned stack). */
  useEffect(() => {
    if (!isOpen) return
    const el = modalOverlayRef.current
    if (el) el.scrollTop = 0
  }, [isOpen, detail])

  const loadRecent = useCallback(async () => {
    setLoading(true)
    try {
      const r = await apiService.listPublishBatches(150)
      setBatches(r.batches || [])
      setActiveSearchQuery(null)
    } catch (e) {
      onToastRef.current('error', e instanceof Error ? e.message : 'Failed to load publish batch history')
      setBatches([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!isOpen) return
    setSearchId('')
    setActiveSearchQuery(null)
    setDetail(null)
    setPendingUnpublishBatchId(null)
    void loadRecent()
    // Intentionally only when the modal opens — not when parent callbacks are recreated (e.g. 30s job refresh).
  }, [isOpen, loadRecent])

  /** Lock layout scroll so the app main scroll area does not shift under the overlay. */
  useEffect(() => {
    if (!isOpen) return
    const html = document.documentElement
    const body = document.body
    const main = document.querySelector('main')
    const prevHtml = html.style.overflow
    const prevBody = body.style.overflow
    const prevMain = main instanceof HTMLElement ? main.style.overflow : ''
    html.style.overflow = 'hidden'
    body.style.overflow = 'hidden'
    if (main instanceof HTMLElement) main.style.overflow = 'hidden'
    return () => {
      html.style.overflow = prevHtml
      body.style.overflow = prevBody
      if (main instanceof HTMLElement) main.style.overflow = prevMain
    }
  }, [isOpen])

  const runSearch = async () => {
    const rid = searchId.trim()
    if (!rid) {
      setActiveSearchQuery(null)
      void loadRecent()
      return
    }
    setLoading(true)
    setActiveSearchQuery(rid)
    try {
      const r = await apiService.searchPublishBatchesByRecordId(rid, 80)
      const list = r.batches || []
      setBatches(list)
      const n = list.length
      onToastRef.current('info', n === 0 ? 'No batches contain that record id' : `Found ${n} batch(es)`)
    } catch (e) {
      setBatches([])
      onToastRef.current('error', e instanceof Error ? e.message : 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  const refreshBatchList = useCallback(async () => {
    const rid = activeSearchQuery?.trim()
    if (rid) {
      setLoading(true)
      try {
        const r = await apiService.searchPublishBatchesByRecordId(rid, 80)
        setBatches(r.batches || [])
      } catch {
        await loadRecent()
      } finally {
        setLoading(false)
      }
    } else {
      await loadRecent()
    }
  }, [activeSearchQuery, loadRecent])

  const openDetail = async (batchId: string) => {
    setLoading(true)
    try {
      const r = await apiService.getPublishBatch(batchId)
      setDetail((r.batch as BatchRow) || null)
    } catch (e) {
      onToastRef.current('error', e instanceof Error ? e.message : 'Failed to load batch')
    } finally {
      setLoading(false)
    }
  }

  const runUnpublishBatch = async (batchId: string) => {
    if (!canEdit || !batchId) return
    setIsUnpublishing(true)
    try {
      const r = await apiService.unpublishPublishBatch(batchId)
      if (r.instance_id) {
        onToastRef.current(
          'success',
          (r.message as string) ||
            'Unpublish job started. This batch will disappear from history after the job completes successfully.'
        )
        await refreshBatchList()
        onUnpublishJobStartedRef.current?.()
      } else {
        onToastRef.current('info', (r.message as string) || 'No documents to unpublish')
      }
    } catch (e) {
      onToastRef.current('error', e instanceof Error ? e.message : 'Unpublish failed')
    } finally {
      setIsUnpublishing(false)
      setPendingUnpublishBatchId(null)
    }
  }

  if (!isOpen || !PORTAL_ROOT) return null

  return createPortal(
    <div
      ref={modalOverlayRef}
      className="fixed inset-0 z-[100] overflow-x-hidden overflow-y-auto overscroll-y-contain"
    >
      <div
        className="absolute inset-0 bg-slate-900/45 backdrop-blur-[2px] transition-opacity"
        onClick={onClose}
        aria-hidden
      />
      <div
        className={
          detail
            ? 'relative z-[1] mx-auto flex min-h-full w-full min-w-0 max-w-5xl flex-col justify-start px-3 pb-10 pt-[max(0.75rem,env(safe-area-inset-top,0px))] sm:px-4 sm:pb-12 sm:pt-[max(1rem,env(safe-area-inset-top,0px))]'
            : 'relative z-[1] mx-auto flex min-h-full w-full min-w-0 max-w-5xl flex-col items-center justify-center px-3 pb-10 pt-[max(0.75rem,env(safe-area-inset-top,0px))] sm:px-4 sm:pb-12 sm:pt-[max(1rem,env(safe-area-inset-top,0px))]'
        }
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="publish-batch-history-title"
          style={PUBLISH_HISTORY_DIALOG_MAX_STYLE}
          className="flex min-h-0 w-full min-w-0 shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_25px_50px_-12px_rgba(15,23,42,0.25)]"
        >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-100 bg-gradient-to-r from-slate-50 via-white to-indigo-50/30 px-5 py-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-md shadow-blue-600/20">
              <History className="h-5 w-5" strokeWidth={2} />
            </div>
            <div className="min-w-0">
              <h2 id="publish-batch-history-title" className="truncate text-lg font-semibold tracking-tight text-slate-900">
                Publish batch history
              </h2>
              <p className="mt-0.5 text-sm text-slate-500">Audit trail, scope, and batch unpublish</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="shrink-0 border-b border-slate-100 bg-slate-50/90 px-5 py-3 sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={searchId}
              onChange={(e) => setSearchId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void runSearch()}
              placeholder="Search by record id…"
              className="min-h-[42px] min-w-[min(100%,220px)] flex-1 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 shadow-sm outline-none ring-blue-500/20 transition-shadow placeholder:text-slate-400 focus:border-blue-400 focus:ring-4"
            />
            <button
              type="button"
              onClick={() => void runSearch()}
              disabled={loading}
              className="inline-flex min-h-[42px] items-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-blue-700 disabled:pointer-events-none disabled:opacity-50"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              Search
            </button>
            <button
              type="button"
              onClick={() => {
                setSearchId('')
                setActiveSearchQuery(null)
                void loadRecent()
              }}
              disabled={loading}
              className="inline-flex min-h-[42px] items-center rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:opacity-50"
            >
              Show all
            </button>
          </div>
        </div>

        {/* Scrollable body: batch list + optional detail (single region so height stays within dialog). */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-5 py-4 sm:px-6">
            {loading && batches.length === 0 ? (
              <div className="flex flex-col items-center gap-3 py-16 text-center text-slate-500">
                <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                <p className="text-sm font-medium text-slate-600">Loading batches…</p>
              </div>
            ) : batches.length > 0 ? (
              <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="overflow-x-auto rounded-xl">
                  <table className="min-w-full text-sm">
                  <thead className="sticky top-0 z-[1] border-b border-slate-200 bg-slate-50/95 backdrop-blur-sm">
                    <tr className="text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                      <th className="whitespace-nowrap px-4 py-3">Created</th>
                      <th className="px-4 py-3">Batch</th>
                      <th className="px-4 py-3">By</th>
                      <th className="whitespace-nowrap px-4 py-3">Docs</th>
                      <th className="whitespace-nowrap px-4 py-3">OK</th>
                      <th className="whitespace-nowrap px-4 py-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {batches.map((b, rowIdx) => {
                      const id = rowBatchId(b)
                      const docs = countDocs(b)
                      const successful = countSuccessful(b)
                      const unpublishLocked = hasBatchUnpublishLocked(b)
                      const canUnpublish =
                        canEdit && !unpublishLocked && hasUnpublishTargets(b) && !isUnpublishing
                      return (
                        <tr
                          key={id || `row-${rowIdx}`}
                          className={
                            rowIdx % 2 === 0
                              ? 'bg-white hover:bg-slate-50/90'
                              : 'bg-slate-50/40 hover:bg-slate-100/80'
                          }
                        >
                          <td className="whitespace-nowrap px-4 py-2.5 text-xs tabular-nums text-slate-600">
                            {b.created_at ? String(b.created_at).slice(0, 19).replace('T', ' ') : '—'}
                          </td>
                          <td
                            className="max-w-[200px] truncate px-4 py-2.5 font-mono text-xs text-slate-800"
                            title={id}
                          >
                            {id || '—'}
                          </td>
                          <td
                            className="max-w-[140px] truncate px-4 py-2.5 text-xs text-slate-700"
                            title={String(b.started_by ?? '')}
                          >
                            {String(b.started_by ?? '—')}
                          </td>
                          <td className="px-4 py-2.5 text-left text-xs font-semibold tabular-nums text-slate-800">
                            {docs}
                          </td>
                          <td className="px-4 py-2.5 text-left text-xs font-semibold tabular-nums text-emerald-700">
                            {successful}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 text-left">
                            <button
                              type="button"
                              className="mr-2 rounded-lg px-2 py-1 text-xs font-semibold text-blue-600 transition-colors hover:bg-blue-50 hover:text-blue-800"
                              onClick={() => void openDetail(id)}
                            >
                              View
                            </button>
                            {canUnpublish ? (
                              <button
                                type="button"
                                className="rounded-lg px-2 py-1 text-xs font-semibold text-amber-800 transition-colors hover:bg-amber-50 hover:text-amber-950"
                                onClick={() => setPendingUnpublishBatchId(id)}
                              >
                                Unpublish
                              </button>
                            ) : null}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
          {!loading && batches.length === 0 && (
            <div className="flex flex-col items-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 px-5 py-12 text-center">
              <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-slate-400">
                <Search className="h-5 w-5" />
              </div>
              <p className="text-sm font-medium text-slate-700">
                {activeSearchQuery ? 'No batches match that record id' : 'No publish batches yet'}
              </p>
              <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-slate-500">
                {activeSearchQuery
                  ? 'Try another id or clear the search to show recent batches.'
                  : 'Completed bulk or retry-failed publish runs will appear here.'}
              </p>
            </div>
          )}
            {detail && (
              <div className="mt-5 border-t border-slate-200 bg-gradient-to-b from-slate-50 to-white pt-5">
                <div className="mx-auto">
                  <div className="relative border-b border-slate-100 pb-5 pt-1">
                <button
                  type="button"
                  className="absolute right-0 top-0 flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-200/60 hover:text-slate-700"
                  onClick={() => setDetail(null)}
                  aria-label="Close detail"
                >
                  <X className="h-4 w-4" />
                </button>
                <div className="mx-auto max-w-2xl px-6 text-center sm:px-10">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-400">Batch detail</p>
                  <div className="mt-2 inline-flex max-w-full items-center justify-center gap-1.5 rounded-full border border-slate-200/80 bg-white/90 px-3 py-1 shadow-sm">
                    <span className="text-[10px] font-medium uppercase tracking-wide text-slate-400">Id</span>
                    <span className="font-mono text-[11px] leading-snug text-slate-600 break-all">
                      {rowBatchId(detail)}
                    </span>
                  </div>
                </div>
              </div>

              {(detailRepo || detailColl) ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {detailRepo ? (
                    <div className="flex gap-3 rounded-xl border border-slate-200/90 bg-white p-3 shadow-sm">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
                        <Library className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Repository</p>
                        <p className="mt-0.5 truncate text-sm font-medium text-slate-900" title={detailRepo}>
                          {detailRepo}
                        </p>
                      </div>
                    </div>
                  ) : null}
                  {detailColl ? (
                    <div className="flex gap-3 rounded-xl border border-slate-200/90 bg-white p-3 shadow-sm">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-violet-50 text-violet-600">
                        <Layers className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Collection</p>
                        <p className="mt-0.5 truncate text-sm font-medium text-slate-900" title={detailColl}>
                          {detailColl}
                        </p>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {detail.skipped ? (
                <div className="mt-3 rounded-lg border border-amber-200/80 bg-amber-50/90 px-3 py-2 text-xs font-medium text-amber-900">
                  Skipped — job was stopped before this batch finished.
                </div>
              ) : null}

              {hasBatchUnpublishLocked(detail) ? (
                <div className="mt-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                  <span className="font-medium text-slate-800">Unpublish requested</span>{' '}
                  {String(detail.unpublish_requested_at).slice(0, 19).replace('T', ' ')}
                  {detail.unpublish_requested_by ? (
                    <span className="text-slate-500"> · {String(detail.unpublish_requested_by)}</span>
                  ) : null}
                </div>
              ) : null}

              <div className="mt-4 text-left">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-slate-600">Total records</span>
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-emerald-800">
                    {detailSuccessRows.length}
                  </span>
                </div>
                <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
                  <div className="overflow-x-auto rounded-xl">
                    <table className="w-full text-left text-xs">
                      <thead className="sticky top-0 z-[1] border-b border-slate-200 bg-slate-50/95 backdrop-blur-sm">
                        <tr className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                          <th className="px-3 py-2">Title</th>
                          <th className="w-[38%] px-3 py-2">Record ID</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {detailSuccessRows.length > 0 ? (
                          detailSuccessRows.map((row, i) => {
                            const rid = row.recordId.trim()
                            const canOpen = Boolean(rid)
                            return (
                              <tr
                                key={`${row.recordId}-${i}`}
                                tabIndex={canOpen ? 0 : undefined}
                                role={canOpen ? 'link' : undefined}
                                aria-label={canOpen ? `Open record ${rid} in review` : undefined}
                                className={
                                  canOpen
                                    ? 'group cursor-pointer text-slate-800 transition-colors hover:bg-blue-50/80 focus:bg-blue-50/80 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-400/60'
                                    : 'text-slate-800'
                                }
                                onClick={() => canOpen && goToRecord(rid)}
                                onKeyDown={(e) => {
                                  if (!canOpen) return
                                  if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault()
                                    goToRecord(rid)
                                  }
                                }}
                              >
                                <td className="px-3 py-2 align-top text-slate-700 leading-snug">
                                  {row.title ? (
                                    <span className="line-clamp-2 text-slate-800">{row.title}</span>
                                  ) : (
                                    <span className="text-slate-400">—</span>
                                  )}
                                </td>
                                <td className="px-3 py-2 align-top">
                                  {canOpen ? (
                                    <span className="inline-flex items-center gap-1 font-mono text-[11px] leading-snug text-blue-700 break-all group-hover:underline underline-offset-2">
                                      {row.recordId}
                                      <ExternalLink className="h-3 w-3 shrink-0 text-blue-500 opacity-70" aria-hidden />
                                    </span>
                                  ) : (
                                    <span className="font-mono text-[11px] text-slate-500">—</span>
                                  )}
                                </td>
                              </tr>
                            )
                          })
                        ) : (
                          <tr>
                            <td colSpan={2} className="px-3 py-6 text-left text-sm text-slate-400">
                              None
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              {detailFailedN > 0 ? (
                <div className="mt-4">
                  <div className="mb-2 flex items-center gap-2">
                    <FileWarning className="h-3.5 w-3.5 text-rose-500" />
                    <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Failed records</h4>
                    <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-rose-800">
                      {detailFailedN}
                    </span>
                  </div>
                  <pre className="max-h-36 overflow-auto rounded-xl border border-slate-200 bg-slate-900/[0.03] p-3 font-mono text-[11px] leading-relaxed text-slate-700 shadow-inner">
                    {JSON.stringify(detail.failed_records ?? [], null, 2)}
                  </pre>
                </div>
              ) : null}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>

      {pendingUnpublishBatchId && (
        <div
          className="fixed inset-0 z-[110] flex items-start justify-center overflow-x-hidden overflow-y-auto overscroll-y-contain p-4 py-8 sm:items-center sm:py-10"
          role="dialog"
          aria-modal="true"
        >
          <button
            type="button"
            className="absolute inset-0 bg-slate-900/50 backdrop-blur-[1px]"
            aria-label="Dismiss"
            onClick={() => !isUnpublishing && setPendingUnpublishBatchId(null)}
          />
          <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl">
            <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-amber-100 text-amber-700">
              <FileWarning className="h-5 w-5" />
            </div>
            <h3 className="text-lg font-semibold tracking-tight text-slate-900">Unpublish this batch?</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-600">
              This starts an unpublish job for the documents in this publish batch (same as other bulk unpublish jobs).
              Ensure no other unpublish job is running.
            </p>
            <p className="mt-3 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-600 break-all">
              {pendingUnpublishBatchId}
            </p>
            <div className="mt-6 flex justify-start gap-2">
              <button
                type="button"
                className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:opacity-50"
                onClick={() => setPendingUnpublishBatchId(null)}
                disabled={isUnpublishing}
              >
                Cancel
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-amber-700 disabled:opacity-50"
                disabled={isUnpublishing}
                onClick={() => void runUnpublishBatch(pendingUnpublishBatchId)}
              >
                {isUnpublishing ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Start unpublish
              </button>
            </div>
          </div>
        </div>
      )}
    </div>,
    PORTAL_ROOT
  )
}
