// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  CalendarRange,
  Clock,
  ListChecks,
  Loader2,
  Pencil,
  Play,
  Save,
  Trash2,
  Zap,
} from 'lucide-react'
import ModalPortal from '@/components/ModalPortal'
import { apiService } from '@/services/api'
import type { StageConfig, FullPipelineConfig } from '../../types'
import DateCalendarField from './DateCalendarField'

/** Flip to `true` to show lookback hours in this modal again (field + last-run row). */
const SHOW_LOOKBACK_HOURS_UI = false

type TabId = 'adhoc' | 'scheduled' | 'history'
type ScheduleRow = Record<string, unknown>

function recurrenceIngestDays(recurrence: unknown): number {
  const r = String(recurrence || 'none').toLowerCase()
  if (r === 'daily') return 1
  if (r === 'weekly') return 7
  if (r === 'monthly') return 30
  if (r === 'yearly') return 365
  return 1
}

function scheduleNextIngestionLine(s: ScheduleRow): string | null {
  const a = String(s.next_ingestion_from_date || '').trim()
  const b = String(s.next_ingestion_to_date || '').trim()
  if (!a || !b) return null
  return `Next ingestion: ${a} → ${b}`
}

/** Days per run for list row: legacy stored ``ingestion_window_days`` if valid, else from recurrence. */
function scheduleIngestDaysForDisplay(s: ScheduleRow): number {
  const raw = s.ingestion_window_days
  if (raw != null && raw !== '') {
    const n = Number.parseInt(String(raw), 10)
    if (Number.isFinite(n) && n >= 1 && n <= 366) return n
  }
  return recurrenceIngestDays(s.recurrence)
}

function formatIsoShort(raw: unknown): string {
  if (raw == null || raw === '') return '—'
  return String(raw).slice(0, 19).replace('T', ' ')
}

function workflowLabel(row: Record<string, unknown>): string {
  const w = String(row.run_workflow || row.run_kind || '—').toLowerCase()
  return w || '—'
}

/** Local calendar YYYY-MM-DD (schedule start date default). */
function localCalendarIsoDate(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Normalize `<input type="time">` value to HH:MM for API (24h UTC). */
function normalizeTimeUtcForApi(raw: string): string {
  const x = raw.trim() || '00:00'
  const m = /^(\d{1,2}):(\d{2})$/.exec(x)
  if (!m) return '00:00'
  const h = Math.min(23, Math.max(0, parseInt(m[1], 10)))
  const min = Math.min(59, Math.max(0, parseInt(m[2], 10)))
  return `${String(h).padStart(2, '0')}:${String(min).padStart(2, '0')}`
}

/** Cosmos / API may store ``HH:MM`` or ``HH:MM:SS``; normalize for ``<input type="time">``. */
function timeInputFromStored(raw: unknown): string {
  const x = String(raw ?? '').trim()
  const m = /^(\d{1,2}):(\d{2})/.exec(x)
  if (!m) return '00:00'
  return normalizeTimeUtcForApi(`${m[1]}:${m[2]}`)
}

function isLegacyFixedWindowSchedule(s: ScheduleRow): boolean {
  return Boolean(String(s.window_from || '').trim() && String(s.window_to || '').trim())
}

interface PeriodicSyncModalProps {
  isOpen: boolean
  globalConfig: StageConfig
  fullPipelineConfig: FullPipelineConfig
  lookbackHoursDraft: string
  onLookbackHoursDraftChange: (value: string) => void
  collectionInput: string
  onCollectionInputChange: (value: string) => void
  fromDateDraft: string
  onFromDateDraftChange: (value: string) => void
  toDateDraft: string
  onToDateDraftChange: (value: string) => void
  onFullPipelineConfigChange: (config: FullPipelineConfig) => void
  onClose: () => void
  onSubmit: () => void
  onSchedulesChanged?: () => void
  /** Refresh pipeline job list from API (e.g. after run-now / process-due). */
  onActiveJobsRefresh?: () => void
  canEdit?: boolean
  onToast?: (type: 'success' | 'error' | 'warning' | 'info', message: string) => void
}

export default function PeriodicSyncModal({
  isOpen,
  globalConfig,
  fullPipelineConfig,
  lookbackHoursDraft,
  onLookbackHoursDraftChange,
  collectionInput,
  onCollectionInputChange,
  fromDateDraft,
  onFromDateDraftChange,
  toDateDraft,
  onToDateDraftChange,
  onFullPipelineConfigChange,
  onClose,
  onSubmit,
  onSchedulesChanged,
  onActiveJobsRefresh,
  canEdit = true,
  onToast,
}: PeriodicSyncModalProps) {
  const onToastRef = useRef(onToast)
  onToastRef.current = onToast

  const [tab, setTab] = useState<TabId>('adhoc')
  const [schedules, setSchedules] = useState<ScheduleRow[]>([])
  const [schedulesLoading, setSchedulesLoading] = useState(false)
  const [scheduleSaving, setScheduleSaving] = useState(false)
  const [processDueLoading, setProcessDueLoading] = useState(false)
  const [schTitle, setSchTitle] = useState('')
  /** First eligible run: UTC calendar day; if already past, next_run_at becomes now (server). */
  const [schStartDate, setSchStartDate] = useState('')
  /** 24h UTC clock for first eligible `next_run_at` (with start date). */
  const [schStartTimeUtc, setSchStartTimeUtc] = useState('00:00')
  const [schRecurrence, setSchRecurrence] = useState('none')
  const [schRunAll, setSchRunAll] = useState(true)
  const [schCollections, setSchCollections] = useState('')
  /** When set, Save updates this document; batch/collection filters/lookback stay as stored unless changed elsewhere. */
  const [editingScheduleId, setEditingScheduleId] = useState<string | null>(null)

  const [runs, setRuns] = useState<Record<string, unknown>[]>([])
  const [runsLoading, setRunsLoading] = useState(false)

  useEffect(() => {
    if (!isOpen) {
      setTab('adhoc')
      setSchStartDate('')
      setSchStartTimeUtc('00:00')
      setEditingScheduleId(null)
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen || tab !== 'scheduled') return
    setSchStartDate((d) => (d.trim() ? d : localCalendarIsoDate()))
    setSchStartTimeUtc((t) => (t.trim() !== '' ? t : '00:00'))
  }, [isOpen, tab])

  const loadSchedules = useCallback(async () => {
    setSchedulesLoading(true)
    try {
      const r = await apiService.listPipelinePeriodicSchedules(100)
      setSchedules((r.schedules as ScheduleRow[]) || [])
    } catch (e) {
      onToastRef.current?.('error', e instanceof Error ? e.message : 'Failed to load schedules')
    } finally {
      setSchedulesLoading(false)
    }
  }, [])

  const loadRuns = useCallback(async () => {
    setRunsLoading(true)
    try {
      const r = await apiService.listPipelinePeriodicRuns(100, {})
      setRuns(r.runs || [])
    } catch (e) {
      onToastRef.current?.('error', e instanceof Error ? e.message : 'Failed to load run history')
    } finally {
      setRunsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!isOpen) return
    if (tab === 'scheduled') void loadSchedules()
    if (tab === 'history') void loadRuns()
  }, [isOpen, tab, loadSchedules, loadRuns])

  const cancelScheduleEdit = () => {
    setEditingScheduleId(null)
    setSchTitle('')
    setSchCollections('')
    setSchRecurrence('none')
    setSchRunAll(true)
    setSchStartDate(localCalendarIsoDate())
    setSchStartTimeUtc('00:00')
  }

  const beginEditSchedule = (s: ScheduleRow) => {
    if (isLegacyFixedWindowSchedule(s)) {
      onToast?.(
        'info',
        'This schedule uses a fixed calendar window (start/end dates). Editing that type is not supported in this form yet.',
      )
      return
    }
    setEditingScheduleId(String(s.id))
    setSchTitle(String(s.title || ''))
    setSchStartDate(String(s.schedule_start_date || '').trim() || localCalendarIsoDate())
    setSchStartTimeUtc(timeInputFromStored(s.schedule_start_time_utc))
    setSchRecurrence(String(s.recurrence || 'none').toLowerCase())
    setSchRunAll(Boolean(s.run_all_stages ?? true))
    const names = s.collection_names
    setSchCollections(
      Array.isArray(names) ? names.map((x) => String(x).trim()).filter(Boolean).join('\n') : '',
    )
  }

  const saveSchedule = async () => {
    if (!schStartDate.trim()) {
      onToast?.('error', 'Choose a Start date (UTC) for when this schedule starts.')
      return
    }
    const collection_names = schCollections
      .split(/[\n,]+/)
      .map((part) => part.trim())
      .filter(Boolean)
    const prev = editingScheduleId
      ? schedules.find((row) => String(row.id) === editingScheduleId)
      : undefined
    setScheduleSaving(true)
    try {
      const collection_ids = Array.isArray(prev?.collection_ids)
        ? (prev.collection_ids as string[])
        : globalConfig.collection_ids || []
      const batch_size =
        prev != null ? Number(prev.batch_size) || 100 : Number(globalConfig.batch_size) || 100
      const parallel_batches =
        prev != null
          ? Number(prev.parallel_batches) || 20
          : Number(globalConfig.parallel_batches) || 20
      let lookback_hours = fullPipelineConfig.lookback_hours
      if (prev != null && prev.lookback_hours != null && prev.lookback_hours !== '') {
        const n = Number(prev.lookback_hours)
        if (Number.isFinite(n)) lookback_hours = n
      }
      await apiService.upsertPipelinePeriodicSchedule({
        ...(editingScheduleId ? { id: editingScheduleId } : {}),
        title: schTitle.trim() || 'Scheduled run',
        enabled: prev != null ? Boolean(prev.enabled ?? true) : true,
        run_all_stages: schRunAll,
        collection_ids,
        collection_names,
        batch_size,
        parallel_batches,
        lookback_hours,
        schedule_start_date: schStartDate.trim(),
        schedule_start_time_utc: normalizeTimeUtcForApi(schStartTimeUtc),
        window_from: '',
        window_to: '',
        recurrence: schRecurrence,
      })
      onToast?.('success', editingScheduleId ? 'Schedule updated' : 'Schedule saved')
      setEditingScheduleId(null)
      setSchTitle('')
      setSchCollections('')
      await loadSchedules()
      onSchedulesChanged?.()
    } catch (e) {
      onToast?.('error', e instanceof Error ? e.message : 'Save failed')
    } finally {
      setScheduleSaving(false)
    }
  }

  const runScheduleNow = async (id: string) => {
    try {
      const r = await apiService.runNowPipelinePeriodicSchedule(id)
      if (r.result?.instance_id) {
        onToast?.('success', 'Pipeline started from schedule')
      } else {
        onToast?.('warning', String(r.result?.message || 'Started with no instance id (check function response).'))
      }
      void loadRuns()
      onActiveJobsRefresh?.()
    } catch (e) {
      onToast?.('error', e instanceof Error ? e.message : 'Run failed')
    }
  }

  const removeSchedule = async (id: string) => {
    if (!confirm('Delete this schedule?')) return
    try {
      await apiService.deletePipelinePeriodicSchedule(id)
      onToast?.('success', 'Schedule deleted')
      setEditingScheduleId((cur) => (cur === id ? null : cur))
      await loadSchedules()
      onSchedulesChanged?.()
    } catch (e) {
      onToast?.('error', e instanceof Error ? e.message : 'Delete failed')
    }
  }

  const processDue = async () => {
    setProcessDueLoading(true)
    try {
      const r = await apiService.processDuePipelinePeriodicSchedules()
      onToast?.('success', `Processed ${r.processed?.length ?? 0} due schedule(s)`)
      await loadSchedules()
      void loadRuns()
      onActiveJobsRefresh?.()
    } catch (e) {
      onToast?.('error', e instanceof Error ? e.message : 'Process due failed')
    } finally {
      setProcessDueLoading(false)
    }
  }

  const scheduleReposBatchForDisplay = useMemo(() => {
    const prevRow =
      editingScheduleId != null
        ? schedules.find((row) => String(row.id) === editingScheduleId)
        : undefined
    const fromStored = prevRow != null && editingScheduleId != null
    const collectionIds = Array.isArray(prevRow?.collection_ids)
      ? (prevRow.collection_ids as string[])
      : globalConfig.collection_ids || []
    const batchSize =
      fromStored && prevRow != null
        ? Number(prevRow.batch_size) || 100
        : Number(globalConfig.batch_size) || 100
    const parallelBatches =
      fromStored && prevRow != null
        ? Number(prevRow.parallel_batches) || 20
        : Number(globalConfig.parallel_batches) || 20
    return {
      collectionIds,
      batchSize,
      parallelBatches,
      heading: fromStored ? 'Repositories (on this schedule)' : 'Repositories (from monitor)',
    }
  }, [
    editingScheduleId,
    schedules,
    globalConfig.collection_ids,
    globalConfig.batch_size,
    globalConfig.parallel_batches,
  ])

  if (!isOpen) return null

  const tabBtn = (id: TabId, label: string, Icon: LucideIcon) => (
    <button
      type="button"
      onClick={() => setTab(id)}
      className={`flex min-w-0 flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-2 text-xs font-medium transition-all sm:px-3 sm:text-sm ${
        tab === id
          ? 'bg-white text-indigo-700 shadow-sm'
          : 'text-gray-600 hover:text-gray-900'
      }`}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 sm:h-4 sm:w-4" />
      <span className="truncate">{label}</span>
    </button>
  )

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[100] overflow-x-hidden overflow-y-auto overscroll-y-contain py-4 sm:py-8">
        <div className="fixed inset-0 bg-gray-500/75 backdrop-blur-sm" onClick={onClose} aria-hidden />
        <div className="relative z-[1] flex min-h-[min(100%,calc(100dvh-2rem))] items-start justify-center px-2 sm:px-4">
          <div className="relative flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-gray-200/80">
            <div className="shrink-0 border-b border-gray-100 bg-gradient-to-r from-slate-50 to-indigo-50/60 px-4 py-3 sm:px-5 sm:py-4">
              <h3 className="text-base font-semibold tracking-tight text-gray-900 sm:text-lg">Periodic sync</h3>
              <p className="mt-0.5 text-[11px] text-gray-600 sm:text-xs">
                Three paths: explicit calendar runs, saved schedules (automation in Data Foundations checks due
                schedules about every minute and starts <span className="font-mono">content-source-periodic-sync</span>), and
                read-only run history — all in Cosmos <span className="font-mono text-gray-500">periodic_run_history</span>.
              </p>
              <div className="mt-3 flex rounded-xl bg-gray-100/90 p-1 ring-1 ring-gray-200/60">
                {tabBtn('adhoc', 'Adhoc run', CalendarRange)}
                {tabBtn('scheduled', 'Scheduled', Clock)}
                {tabBtn('history', 'Batch runs', ListChecks)}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3 sm:px-5 sm:py-4">
              {tab === 'adhoc' && (
                <div className="space-y-4">
                  <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-3">
                    <p className="text-xs font-semibold text-indigo-900">Adhoc run</p>
                    <p className="mt-1 text-[11px] leading-snug text-indigo-900/85">
                      <span className="font-semibold text-indigo-950">From date</span> and{' '}
                      <span className="font-semibold text-indigo-950">To date</span> set the ingestion window for this
                      one-off run. Repositories, batch size, and parallel batches come from the pipeline monitor above.
                      Runs are tagged <span className="font-mono">adhoc</span> in history.
                    </p>
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-gray-50/40 p-3">
                    <p className="text-xs font-semibold text-gray-800">Repositories (from monitor)</p>
                    <p className="mt-1 text-[11px] text-gray-600">
                      {(globalConfig.collection_ids || []).length > 0
                        ? (globalConfig.collection_ids || []).join(', ')
                        : 'All repositories'}{' '}
                      · batch {globalConfig.batch_size || 100} · parallel {globalConfig.parallel_batches ?? 20}
                    </p>
                  </div>

                  <div className={SHOW_LOOKBACK_HOURS_UI ? '' : 'hidden'} aria-hidden={!SHOW_LOOKBACK_HOURS_UI}>
                    <label className="mb-0.5 block text-xs font-medium text-gray-700">Lookback hours</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={lookbackHoursDraft}
                      onChange={(e) => onLookbackHoursDraftChange(e.target.value)}
                      className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:items-start sm:gap-x-4 sm:gap-y-3">
                    <DateCalendarField
                      id="adhoc-from"
                      label="From date"
                      value={fromDateDraft}
                      onChange={onFromDateDraftChange}
                      placeholder="YYYY-MM-DD"
                    />
                    <DateCalendarField
                      id="adhoc-to"
                      label="To date"
                      value={toDateDraft}
                      onChange={onToDateDraftChange}
                      placeholder="YYYY-MM-DD"
                    />
                  </div>

                  <div>
                    <label className="mb-0.5 block text-xs font-medium text-gray-700">
                      Collection names <span className="font-normal text-gray-400">(optional)</span>
                    </label>
                    <textarea
                      value={collectionInput}
                      onChange={(e) => onCollectionInputChange(e.target.value)}
                      rows={2}
                      placeholder="Comma or newline separated"
                      className="w-full resize-none rounded-lg border border-gray-300 px-2.5 py-1.5 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>

                  <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-gray-200 bg-white p-3 hover:border-indigo-200">
                    <input
                      type="checkbox"
                      checked={fullPipelineConfig.run_all_stages}
                      onChange={(e) =>
                        onFullPipelineConfigChange({ ...fullPipelineConfig, run_all_stages: e.target.checked })
                      }
                      className="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    <span className="text-sm font-medium leading-snug text-gray-800">
                      Run all processing stages after sync
                    </span>
                  </label>

                  <p className="text-[11px] leading-snug text-gray-500">
                    Both From and To are required. For the next chunk of work, set From to the day after the previous
                    run’s To (or any new range).
                  </p>
                </div>
              )}

              {tab === 'scheduled' && (
                <div className="space-y-4">
                  <div className="rounded-xl border border-gray-200 bg-gray-50/40 p-3">
                    <p className="text-xs font-semibold text-gray-800">{scheduleReposBatchForDisplay.heading}</p>
                    <p className="mt-1 text-[11px] text-gray-600">
                      {scheduleReposBatchForDisplay.collectionIds.length > 0
                        ? scheduleReposBatchForDisplay.collectionIds.join(', ')
                        : 'All repositories'}{' '}
                      · batch {scheduleReposBatchForDisplay.batchSize} · parallel{' '}
                      {scheduleReposBatchForDisplay.parallelBatches}
                    </p>
                    <p className="mt-1 text-[10px] leading-snug text-gray-500">
                      {editingScheduleId
                        ? 'These values are stored on the schedule and used when it runs. Save from the form only changes title, window, repeat, collections, and run-all.'
                        : 'Uses the pipeline monitor settings above when you save a new schedule.'}
                    </p>
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-white p-3 shadow-sm sm:p-4">
                    <p className="text-xs font-semibold text-gray-900">
                      {editingScheduleId ? 'Edit schedule' : 'New schedule'}
                    </p>
                    <p className="mt-1 text-[11px] leading-snug text-gray-500">
                      <span className="font-semibold text-gray-800">Start date (UTC)</span> plus optional{' '}
                      <span className="font-semibold text-gray-800">Start time (UTC)</span> set the first eligible{' '}
                      <span className="font-mono">next_run_at</span>.{' '}
                      <span className="font-semibold text-gray-800">Repeat</span> is the schedule &quot;To&quot; — how
                      often it runs and how many UTC calendar days each run covers (daily 1, weekly 7, monthly 30,
                      yearly 365, one-time 1).
                    </p>
                    <div className="mt-4 flex flex-col gap-3">
                      <div>
                        <label className="mb-1 block text-sm font-medium text-gray-700">Title</label>
                        <input
                          value={schTitle}
                          onChange={(e) => setSchTitle(e.target.value)}
                          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
                          placeholder="e.g. Weekly Content Source catch-up"
                        />
                      </div>
                      {/* Paired columns: label + control each — aligns Start date and Repeat on desktop; correct order on mobile */}
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:items-start sm:gap-x-4">
                        <div className="flex min-w-0 flex-col gap-2">
                          <label
                            htmlFor="sch-start-date-trigger"
                            className="text-sm font-medium leading-snug text-gray-700"
                          >
                            Start date (UTC)
                          </label>
                          <DateCalendarField
                            showLabel={false}
                            id="sch-start-date-trigger"
                            label="Start date (UTC)"
                            value={schStartDate}
                            onChange={setSchStartDate}
                            placeholder="YYYY-MM-DD"
                          />
                          <label htmlFor="sch-start-time-utc" className="text-sm font-medium leading-snug text-gray-700">
                            Start time (UTC)
                          </label>
                          <input
                            id="sch-start-time-utc"
                            type="time"
                            step={60}
                            value={schStartTimeUtc}
                            onChange={(e) => setSchStartTimeUtc(e.target.value)}
                            className="h-10 w-full rounded-lg border border-gray-300 bg-white px-3 font-mono text-sm text-gray-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
                            title="24-hour UTC (same clock you would use in UTC, not your local zone)"
                          />
                          <p className="text-[10px] leading-snug text-gray-500">
                            Optional. Uses UTC wall time with the start date (default 00:00). Values are sent as-is in
                            HH:MM — interpret as UTC.
                          </p>
                        </div>
                        <div className="flex min-w-0 flex-col gap-2">
                          <label
                            htmlFor="sch-repeat"
                            className="text-sm font-medium leading-snug text-gray-700"
                          >
                            Repeat
                          </label>
                          <select
                            id="sch-repeat"
                            value={schRecurrence}
                            onChange={(e) => setSchRecurrence(e.target.value)}
                            className="h-10 w-full min-w-0 rounded-lg border border-gray-300 bg-white px-3 text-sm text-gray-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
                          >
                            <option value="none">One time</option>
                            <option value="daily">Daily (1-day window)</option>
                            <option value="weekly">Weekly (7-day window)</option>
                            <option value="monthly">Monthly (30-day window)</option>
                            <option value="yearly">Yearly (365-day window)</option>
                          </select>
                        </div>
                      </div>
                      <label className="flex cursor-pointer items-start gap-2.5">
                        <input
                          type="checkbox"
                          checked={schRunAll}
                          onChange={(e) => setSchRunAll(e.target.checked)}
                          className="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                        />
                        <span className="text-sm font-medium leading-snug text-gray-800">
                          Run all pipeline stages
                        </span>
                      </label>
                      <div>
                        <label className="mb-1 block text-sm font-medium text-gray-700">
                          Collections <span className="font-normal text-gray-400">(optional)</span>
                        </label>
                        <textarea
                          value={schCollections}
                          onChange={(e) => setSchCollections(e.target.value)}
                          rows={2}
                          className="w-full resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
                          placeholder="Comma or newline separated"
                        />
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={!canEdit || scheduleSaving}
                        onClick={() => void saveSchedule()}
                        className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {scheduleSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                        {editingScheduleId ? 'Update schedule' : 'Save schedule'}
                      </button>
                      {editingScheduleId ? (
                        <button
                          type="button"
                          disabled={scheduleSaving}
                          onClick={() => cancelScheduleEdit()}
                          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-50"
                        >
                          Cancel edit
                        </button>
                      ) : null}
                      <button
                        type="button"
                        disabled={processDueLoading}
                        onClick={() => void processDue()}
                        className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-50"
                      >
                        {processDueLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        Run due now
                      </button>
                    </div>
                    <p className="mt-2 text-[11px] leading-snug text-gray-500">
                      <span className="font-medium text-gray-700">Run due now</span> calls the same Archivist API as the
                      Data Foundations poller: each overdue schedule is started once, and{' '}
                      <span className="font-mono">next_run_at</span> advances after a successful start (with an instance
                      id). Use this button to catch up or test. When the function app is running, due schedules are
                      picked up automatically (about every minute). If the app is down or sync is already running, a
                      due window can slip until the next successful start. Errors or missing instance ids leave the
                      schedule due for retry. <span className="font-medium text-gray-700">Run now</span> on a row starts
                      the sync immediately and advances <span className="font-mono">next_run_at</span> by one repeat
                      step from the <span className="font-medium text-gray-700">planned</span> run time (not from
                      click time), so an early run keeps the same cadence (e.g. daily stays on the same clock).
                    </p>
                  </div>

                  <div className="overflow-hidden rounded-xl border border-gray-200">
                    <div className="border-b border-gray-100 bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-700">
                      Saved schedules
                    </div>
                    {schedulesLoading ? (
                      <div className="flex justify-center py-8 text-gray-500">
                        <Loader2 className="h-6 w-6 animate-spin" />
                      </div>
                    ) : schedules.length === 0 ? (
                      <p className="px-3 py-6 text-center text-sm text-gray-500">No schedules yet.</p>
                    ) : (
                      <ul className="max-h-56 divide-y divide-gray-100 overflow-y-auto">
                        {schedules.map((s) => {
                          const nextIngestionLine = scheduleNextIngestionLine(s)
                          return (
                          <li
                            key={String(s.id)}
                            className={`flex flex-col gap-2 px-3 py-2.5 text-xs sm:flex-row sm:items-center sm:justify-between ${
                              editingScheduleId === String(s.id) ? 'bg-indigo-50/60' : ''
                            }`}
                          >
                            <div className="min-w-0">
                              <p className="truncate font-medium text-gray-900">{String(s.title || s.id)}</p>
                              <p className="text-[11px] text-gray-500">
                                {String(s.window_from || '').trim() && String(s.window_to || '').trim()
                                  ? `Start ${String(s.window_from)} → End ${String(s.window_to)} (fixed)`
                                  : (() => {
                                      const t = String(s.schedule_start_time_utc || '00:00').trim() || '00:00'
                                      const timeSuffix = t !== '00:00' ? ` @ ${t}` : ''
                                      return `Start ${String(s.schedule_start_date || '—')}${timeSuffix} (UTC) · Repeat ${String(s.recurrence || 'none')} · ${scheduleIngestDaysForDisplay(s)}d/run`
                                    })()}
                              </p>
                              <p className="text-[11px] text-gray-400">Next: {String(s.next_run_at || '—')}</p>
                              {nextIngestionLine ? (
                                <p className="text-[11px] text-gray-500">{nextIngestionLine}</p>
                              ) : null}
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-1.5">
                              <button
                                type="button"
                                disabled={!canEdit || isLegacyFixedWindowSchedule(s)}
                                onClick={() => beginEditSchedule(s)}
                                title={
                                  isLegacyFixedWindowSchedule(s)
                                    ? 'Fixed-window schedules cannot be edited here'
                                    : 'Load into form above'
                                }
                                className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2 py-1 text-[11px] font-medium text-gray-800 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <Pencil className="h-3 w-3" />
                                Edit
                              </button>
                              <button
                                type="button"
                                disabled={!canEdit}
                                onClick={() => void runScheduleNow(String(s.id))}
                                className="rounded-lg bg-emerald-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                              >
                                Run now
                              </button>
                              <button
                                type="button"
                                disabled={!canEdit}
                                onClick={() => void removeSchedule(String(s.id))}
                                className="rounded-lg border border-red-200 p-1 text-red-700 hover:bg-red-50 disabled:opacity-50"
                                title="Delete"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          </li>
                          )
                        })}
                      </ul>
                    )}
                  </div>
                </div>
              )}

              {tab === 'history' && (
                <div className="space-y-2">
                  <p className="text-xs text-gray-600">
                    Read-only audit of stored runs (workflow, window, repos/collections, completion). New work starts
                    from Adhoc or Scheduled, not here.
                  </p>
                  <div className="overflow-x-auto rounded-xl border border-gray-200">
                    {runsLoading ? (
                      <div className="flex justify-center py-10">
                        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
                      </div>
                    ) : (
                      <table className="w-full min-w-[640px] text-left text-[11px] sm:text-xs">
                        <thead className="sticky top-0 bg-gray-50 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
                          <tr>
                            <th className="whitespace-nowrap px-2 py-2">Workflow</th>
                            <th className="whitespace-nowrap px-2 py-2">From → To</th>
                            <th className="whitespace-nowrap px-2 py-2">Started</th>
                            <th className="whitespace-nowrap px-2 py-2">Ended</th>
                            <th className="px-2 py-2">Repos / cols</th>
                            <th className="whitespace-nowrap px-2 py-2">Records</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {runs.map((row) => (
                            <tr key={String(row.id ?? row.orchestration_instance_id)} className="hover:bg-gray-50/80">
                              <td className="whitespace-nowrap px-2 py-2 font-medium capitalize text-indigo-800">
                                {workflowLabel(row)}
                              </td>
                              <td className="whitespace-nowrap px-2 py-2 font-mono text-gray-800">
                                {String(row.from_date ?? '—')} → {String(row.to_date ?? '—')}
                              </td>
                              <td className="whitespace-nowrap px-2 py-2 font-mono text-gray-600">
                                {formatIsoShort(row.started_at)}
                              </td>
                              <td className="whitespace-nowrap px-2 py-2 font-mono text-gray-600">
                                {row.completed_at ? formatIsoShort(row.completed_at) : '—'}
                              </td>
                              <td className="max-w-[200px] truncate px-2 py-2 text-gray-600" title={String(row.collection_ids)}>
                                {Array.isArray(row.repository_names) && (row.repository_names as string[]).length
                                  ? (row.repository_names as string[]).join(', ')
                                  : Array.isArray(row.collection_ids)
                                    ? (row.collection_ids as string[]).join(', ')
                                    : '—'}
                                {Array.isArray(row.collection_names) && (row.collection_names as string[]).length ? (
                                  <span className="block truncate text-gray-400">
                                    cols: {(row.collection_names as string[]).join(', ')}
                                  </span>
                                ) : null}
                              </td>
                              <td className="whitespace-nowrap px-2 py-2 text-gray-700">
                                {row.records_ingested != null ? String(row.records_ingested) : '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-gray-100 bg-gray-50/80 px-4 py-3 sm:px-5">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-200/80"
              >
                Close
              </button>
              {tab === 'adhoc' && (
                <button
                  type="button"
                  onClick={onSubmit}
                  disabled={!canEdit}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-600 to-violet-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:from-indigo-700 hover:to-violet-700 disabled:opacity-50"
                >
                  <Zap className="h-3.5 w-3.5" />
                  Start adhoc pipeline
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </ModalPortal>
  )
}
