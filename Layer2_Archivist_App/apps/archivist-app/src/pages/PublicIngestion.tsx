// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  Play,
  Loader2,
  CheckCircle2,
  XCircle,
  RefreshCw,
  RotateCcw,
  Download,
  Database,
  Clock,
  AlertTriangle,
  BookOpen,
  Calendar,
  FileArchive,
  ChevronDown,
  ChevronRight,
  Trash2,
  Zap,
  Square,
} from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import { apiService, DigitalIngestionJob } from '@/services/api'

// =============================================================================
// Pipeline stage definitions (mirrors the 3 public resource sources)
// =============================================================================

interface IngestionStep {
  label: string
  description: string
  disabled?: boolean
}

interface IngestionStage {
  id: string
  name: string
  description: string
  icon: React.ComponentType<{ className?: string }>
  sourceKey: string
  requiresOcr: boolean
  steps: IngestionStep[]
}

const STAGES: IngestionStage[] = [
  {
    id: 'tr-cyclopedia',
    name: 'Theodore Roosevelt Cyclopedia',
    description: 'Fetch TRC pages, store text in blob, save to Cosmos, and publish to search',
    icon: BookOpen,
    sourceKey: 'tr-cyclopedia',
    requiresOcr: false,
    steps: [
      { label: 'Fetch & Parse', description: 'Scrape source pages, extract entries, upload text to Blob Storage, and save to Cosmos DB' },
      { label: 'OCR & Store Assets', description: 'Not applicable — text extracted during fetch', disabled: true },
      { label: 'Ingest to Search Index', description: 'Publish items from Cosmos DB to Azure AI Search for full-text search' },
    ],
  },
  {
    id: 'moore-chronology',
    name: 'The Moore Chronology',
    description: 'Download PDFs, run GPT-4 OCR, save to Cosmos, and publish to search',
    icon: Calendar,
    sourceKey: 'moore-chronology',
    requiresOcr: true,
    steps: [
      { label: 'Fetch & Parse', description: 'Scrape source pages, extract chronology entries, and save to Cosmos DB' },
      { label: 'OCR & Store Assets', description: 'Run GPT-4 Vision OCR on PDF volumes, store results in Blob Storage, and update Cosmos DB' },
      { label: 'Ingest to Search Index', description: 'Publish items from Cosmos DB to Azure AI Search for full-text search' },
    ],
  },
  {
    id: 'genealogy-papers',
    name: 'Digitized Genealogy & Papers by the TRA',
    description: 'Fetch TRA pages, store text in blob, save to Cosmos, and publish to search',
    icon: FileArchive,
    sourceKey: 'genealogy-papers',
    requiresOcr: false,
    steps: [
      { label: 'Fetch & Parse', description: 'Scrape TRA pages, extract entries, upload text to Blob Storage, and save to Cosmos DB' },
      { label: 'OCR & Store Assets', description: 'Not applicable — text extracted during fetch', disabled: true },
      { label: 'Ingest to Search Index', description: 'Publish items from Cosmos DB to Azure AI Search for full-text search' },
    ],
  },
]

// =============================================================================
// Step → backend mapping (1:1 — each UI step maps to one backend step)
// =============================================================================

const STEP_BACKEND_MAP = ['fetch', 'ocr', 'publish'] as const

function getBackendStep(_sourceKey: string, stepIdx: number): string {
  return STEP_BACKEND_MAP[stepIdx] ?? 'publish'
}

// =============================================================================
// Helpers
// =============================================================================

function friendlyError(raw: string): string {
  if (raw.includes('Owner resource does not exist') || raw.includes('CosmosResourceNotFoundError')) {
    return 'Database container not found. Verify Cosmos DB configuration and try again.'
  }
  if (raw.includes('RequestTimeout') || raw.includes('timeout')) {
    return 'Request timed out. Please wait a moment and retry.'
  }
  if (raw.includes('401') || raw.includes('Unauthorized') || raw.includes('AuthenticationError')) {
    return 'Authentication failed. Check service credentials.'
  }
  if (raw.includes('403') || raw.includes('Forbidden')) {
    return 'Access denied. Insufficient permissions.'
  }
  if (raw.includes('429') || raw.includes('TooManyRequests')) {
    return 'Rate limited. Wait a few seconds and retry.'
  }
  if (raw.includes('ServiceUnavailable') || raw.includes('503')) {
    // Only use generic message for short raw exception strings.
    // If the message is already a long descriptive string (set by the API),
    // return it directly so operator guidance is not lost.
    if (raw.length < 120) {
      return 'Service temporarily unavailable. Retry shortly.'
    }
  }
  if (raw.length > 300) {
    return raw.slice(0, 300) + '...'
  }
  return raw
}

function generateJobReport(job: DigitalIngestionJob, stage: IngestionStage, totalInDb: number): string {
  const now = new Date().toLocaleString()
  const line = '-'.repeat(60)
  let r = ''
  r += `${'='.repeat(60)}\n`
  r += `  INGESTION REPORT - ${stage.name}\n`
  r += `${'='.repeat(60)}\n\n`

  r += `  Report Generated:  ${now}\n`
  r += `  Pipeline:          ${stage.name}\n`
  r += `  Description:       ${stage.description}\n\n`

  // Status summary
  r += `${line}\n  RESULT\n${line}\n\n`
  const statusText = job.status === 'Completed' ? 'Successfully completed' : job.status === 'Failed' ? 'Failed - see details below' : 'In progress'
  r += `  ${statusText}\n\n`
  r += `  Items ingested this run:   ${job.items_ingested.toLocaleString()}\n`
  r += `  Total items in database:   ${totalInDb.toLocaleString()}\n`
  if (job.started_at) r += `  Started at:                ${new Date(job.started_at).toLocaleString()}\n`
  if (job.completed_at) r += `  Finished at:               ${new Date(job.completed_at).toLocaleString()}\n`
  if (job.started_at && job.completed_at) {
    const secs = Math.round((new Date(job.completed_at).getTime() - new Date(job.started_at).getTime()) / 1000)
    const mins = Math.floor(secs / 60)
    const rem = secs % 60
    r += `  Duration:                  ${mins > 0 ? `${mins}m ${rem}s` : `${secs}s`}\n`
  }
  r += '\n'

  // Pipeline steps
  r += `${line}\n  PIPELINE STEPS\n${line}\n\n`
  stage.steps.forEach((step, i) => {
    const failedAtFirstStep =
      job.status === 'Failed' &&
      typeof job.error_message === 'string' &&
      (job.error_message.includes('503') ||
        job.error_message.includes('temporarily unavailable') ||
        job.error_message.includes('Data Foundations') ||
        job.error_message.includes('unreachable') ||
        job.error_message.includes('timed out') ||
        job.error_message.includes('timeout') ||
        job.error_message.includes('RequestTimeout'))
    const failedStepIdx = failedAtFirstStep ? 0 : stage.steps.length - 1
    const done = job.status === 'Completed' || (job.status === 'Failed' && i < failedStepIdx)
    const failed = job.status === 'Failed' && i === failedStepIdx
    const marker = done ? '[done]' : failed ? '[failed]' : '[pending]'
    r += `  ${i + 1}. ${marker}  ${step.label}\n`
    r += `       ${step.description}\n\n`
  })

  // OCR details
  if (job.ocr_status !== 'pending' && job.ocr_status !== 'skipped') {
    r += `${line}\n  OCR PROCESSING\n${line}\n\n`
    const ocrLabel = job.ocr_status === 'completed' ? 'Completed' : job.ocr_status === 'failed' ? 'Failed' : 'Running'
    r += `  Status:     ${ocrLabel}\n`
    r += `  Processed:  ${job.ocr_items_processed} of ${job.ocr_items_total}\n`
    if (job.ocr_items_failed > 0) r += `  Failed:     ${job.ocr_items_failed}\n`
    if (job.ocr_error) r += `  Error:      ${job.ocr_error}\n`
    r += '\n'
  }

  // Error details
  if (job.error_message) {
    r += `${line}\n  ERROR DETAILS\n${line}\n\n`
    r += `  ${friendlyError(job.error_message)}\n\n`
  }

  r += `${'='.repeat(60)}\n  END OF REPORT\n${'='.repeat(60)}\n`
  return r
}
function downloadReport(content: string, sourceKey: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `ingestion-report-${sourceKey}-${new Date().toISOString().slice(0, 10)}.txt`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// =============================================================================
// Status Badge (matches Data Pipeline)
// =============================================================================

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    Running: 'bg-blue-100 text-blue-700',
    Completed: 'bg-green-100 text-green-700',
    Failed: 'bg-red-100 text-red-700',
    Stopped: 'bg-amber-100 text-amber-700',
    never_run: 'bg-gray-100 text-gray-500',
  }
  const labels: Record<string, string> = {
    Running: 'Running',
    Completed: 'Completed',
    Failed: 'Failed',
    Stopped: 'Stopped',
    never_run: 'Never run',
  }
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${styles[status] || 'bg-gray-100 text-gray-500'}`}>
      {status === 'Running' && <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse mr-1 align-middle" />}
      {labels[status] || status}
    </span>
  )
}

// =============================================================================
// Stat Card (matches Data Pipeline)
// =============================================================================

function StatCard({ icon, iconBg, label, value, valueColor = 'text-gray-900' }: {
  icon: React.ReactNode; iconBg: string; label: string; value: string | number; valueColor?: string
}) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
      <div className="flex items-center gap-3">
        <div className={`p-2 rounded-lg ${iconBg}`}>{icon}</div>
        <div>
          <p className="text-sm text-gray-500">{label}</p>
          <p className={`text-2xl font-bold ${valueColor}`}>
            {typeof value === 'number' ? value.toLocaleString() : value}
          </p>
        </div>
      </div>
    </div>
  )
}

// =============================================================================
// OCR Substep indicator
// =============================================================================

function OcrSubstep({ job }: { job: DigitalIngestionJob }) {
  if (job.ocr_status === 'pending' || job.ocr_status === 'skipped') return null
  const icon = {
    running: <Loader2 className="w-3.5 h-3.5 text-blue-600 animate-spin" />,
    completed: <CheckCircle2 className="w-3.5 h-3.5 text-green-600" />,
    failed: <XCircle className="w-3.5 h-3.5 text-red-600" />,
  }[job.ocr_status]
  const label = {
    running: `OCR: ${job.ocr_items_processed}/${job.ocr_items_total}`,
    completed: `OCR: ${job.ocr_items_processed} done`,
    failed: `OCR failed${job.ocr_error ? ': ' + job.ocr_error.slice(0, 60) : ''}`,
  }[job.ocr_status]
  return (
    <div className="flex items-center gap-1.5 text-xs text-gray-500 mt-1">
      {icon}
      <span>{label}</span>
    </div>
  )
}

// =============================================================================
// Main Component
// =============================================================================

const PublicIngestionPage: React.FC = () => {
  const [jobs, setJobs] = useState<Record<string, DigitalIngestionJob>>({})
  const [sourceCounts, setSourceCounts] = useState<Record<string, number>>({})
  const [triggeringStage, setTriggeringStage] = useState<string | null>(null)
  const [triggeringStep, setTriggeringStep] = useState<string | null>(null)  // "sourceKey:stepIdx"
  const [stoppingStage, setStoppingStage] = useState<string | null>(null)
  const [expandedStage, setExpandedStage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const result = await apiService.getDigitalIngestionStatus()
      setJobs(result.jobs || {})
      setLastUpdated(new Date())
    } catch {
      // Silently fail
    }
  }, [])

  const fetchCounts = useCallback(async () => {
    try {
      const summary = await apiService.getDigitalItemsSummary()
      setSourceCounts(summary)
    } catch {
      // Silently fail
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    fetchCounts()
  }, [fetchStatus, fetchCounts])

  // Poll while any job is running
  useEffect(() => {
    const hasRunning = Object.values(jobs).some((j) => j.status === 'Running')
    if (hasRunning) {
      if (!pollingRef.current) {
        pollingRef.current = setInterval(() => {
          fetchStatus()
          fetchCounts()
        }, 3000)
      }
    } else {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [jobs, fetchStatus, fetchCounts])

  const handleTrigger = async (sourceKey: string) => {
    setError(null)
    setTriggeringStage(sourceKey)
    try {
      await apiService.startDigitalIngestion(sourceKey)
      // Optimistically set to Running so polling starts immediately
      // (background thread may not have registered state yet)
      const stage = STAGES.find(st => st.sourceKey === sourceKey)
      setJobs(prev => ({
        ...prev,
        [sourceKey]: {
          source: sourceKey,
          label: stage?.name ?? sourceKey,
          status: 'Running',
          started_at: new Date().toISOString(),
          completed_at: null,
          output_lines: [],
          error_message: null,
          items_ingested: prev[sourceKey]?.items_ingested ?? 0,
          ocr_status: 'pending',
          ocr_items_processed: 0,
          ocr_items_failed: 0,
          ocr_items_total: 0,
          ocr_error: null,
        }
      }))
      // Give backend thread ~1s to register before first status sync
      await new Promise(r => setTimeout(r, 1000))
      await fetchStatus()
    } catch (e: unknown) {
      setError(friendlyError(e instanceof Error ? e.message : 'Failed to start ingestion'))
      // Revert optimistic update on error
      await fetchStatus()
    } finally {
      setTriggeringStage(null)
    }
  }

  const handleStepTrigger = async (sourceKey: string, stepIdx: number) => {
    const backendStep = getBackendStep(sourceKey, stepIdx)
    const stepKey = `${sourceKey}:${stepIdx}`
    setError(null)
    setTriggeringStep(stepKey)
    try {
      await apiService.startDigitalIngestion(sourceKey, backendStep)
      const stage = STAGES.find(st => st.sourceKey === sourceKey)
      setJobs(prev => ({
        ...prev,
        [sourceKey]: {
          source: sourceKey,
          label: stage?.name ?? sourceKey,
          status: 'Running',
          started_at: new Date().toISOString(),
          completed_at: null,
          output_lines: [],
          error_message: null,
          items_ingested: prev[sourceKey]?.items_ingested ?? 0,
          current_step: backendStep as 'fetch' | 'ocr' | 'publish',
          ocr_status: 'pending',
          ocr_items_processed: 0,
          ocr_items_failed: 0,
          ocr_items_total: 0,
          ocr_error: null,
        }
      }))
      await new Promise(r => setTimeout(r, 1000))
      await fetchStatus()
    } catch (e: unknown) {
      setError(friendlyError(e instanceof Error ? e.message : `Failed to run step: ${backendStep}`))
      await fetchStatus()
    } finally {
      setTriggeringStep(null)
    }
  }

  const handleStop = async (sourceKey: string) => {
    setError(null)
    setStoppingStage(sourceKey)
    try {
      await apiService.stopDigitalIngestion(sourceKey)
      // Optimistically mark as Stopped
      setJobs(prev => prev[sourceKey]
        ? { ...prev, [sourceKey]: { ...prev[sourceKey], status: 'Stopped', completed_at: new Date().toISOString() } }
        : prev
      )
      await fetchStatus()
    } catch (e: unknown) {
      setError(friendlyError(e instanceof Error ? e.message : 'Failed to stop ingestion'))
    } finally {
      setStoppingStage(null)
    }
  }

  const handleTriggerAll = async () => {
    setError(null)
    setTriggeringStage('all')
    try {
      const result = await apiService.startDigitalIngestion('all')
      // Optimistically mark all triggered sources as Running immediately.
      // The backend starts each source in a separate thread; by the time
      // fetchStatus() is called, not all threads may have registered their
      // job entries yet (race condition). This ensures all three rows light
      // up right away, and the subsequent poll will sync actual state.
      if (result.sources?.length) {
        setJobs(prev => {
          const next = { ...prev }
          for (const s of result.sources) {
            const stage = STAGES.find(st => st.sourceKey === s)
            // Always overwrite — re-run should reset to Running state
            next[s] = {
              source: s,
              label: stage?.name ?? s,
              status: 'Running',
              started_at: new Date().toISOString(),
              completed_at: null,
              output_lines: [],
              error_message: null,
              items_ingested: next[s]?.items_ingested ?? 0,
              ocr_status: 'pending',
              ocr_items_processed: 0,
              ocr_items_failed: 0,
              ocr_items_total: 0,
              ocr_error: null,
            }
          }
          return next
        })
      }
      // Give backend threads ~1s to register before first status sync
      await new Promise(r => setTimeout(r, 1000))
      await fetchStatus()
    } catch (e: unknown) {
      setError(friendlyError(e instanceof Error ? e.message : 'Failed to start ingestion'))
    } finally {
      setTriggeringStage(null)
    }
  }

  const handleClearAll = async () => {
    try {
      await apiService.clearAllDigitalIngestionJobs()
      await fetchStatus()
    } catch {
      // ignore
    }
  }

  // Compute stats
  const jobList = Object.values(jobs)
  const hasAnyRunning = jobList.some((j) => j.status === 'Running')
  const totalItems = Object.values(sourceCounts).reduce((a, b) => a + b, 0)
  const runningCount = jobList.filter(j => j.status === 'Running').length
  const completedCount = jobList.filter(j => j.status === 'Completed').length
  const failedCount = jobList.filter(j => j.status === 'Failed').length
  const jobCount = jobList.length

  return (
    <div className="bg-gray-50 min-h-full">
      <PageHeader
        title="Public Ingestion"
        subtitle="Run and monitor public resource ingestion pipelines"
      />

      {/* Quick Actions Bar */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            {/* Run All Pipeline */}
            <button
              onClick={handleTriggerAll}
              disabled={triggeringStage !== null}
              className="inline-flex items-center gap-2 px-4 py-2 bg-linear-to-r from-indigo-600 to-purple-600 text-white rounded-lg font-medium hover:from-indigo-700 hover:to-purple-700 transition-all shadow-md hover:shadow-lg disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {triggeringStage === 'all' ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Zap className="w-4 h-4" />
              )}
              Run All Sources
            </button>

            {hasAnyRunning && (
              <span className="inline-flex items-center gap-1.5 text-xs text-blue-600 font-medium">
                <span className="w-2 h-2 rounded-full bg-blue-600 animate-pulse" />
                Processing...
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {hasAnyRunning && (
              <span className="text-xs text-amber-600 hidden sm:inline">
                Jobs running • Auto-refresh every 3s
              </span>
            )}
            <button
              onClick={() => { fetchStatus(); fetchCounts() }}
              className="inline-flex items-center gap-2 px-3 py-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
              title="Refresh status"
            >
              <RefreshCw className="w-4 h-4" />
              Refresh
            </button>
          </div>
        </div>
      </div>

      <div className="px-6 py-6">
        {/* Error banner */}
        {error && (
          <div className="mb-6 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-center gap-2">
            <XCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}

        {/* Statistics Overview */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Statistics Overview</h2>
          {lastUpdated && (
            <span className="text-sm text-gray-500">
              Last updated: {lastUpdated.toLocaleString()}
            </span>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
          <StatCard
            icon={<Database className="w-5 h-5 text-indigo-600" />}
            iconBg="bg-indigo-100"
            label="Total Items"
            value={totalItems}
          />
          <StatCard
            icon={<Clock className="w-5 h-5 text-blue-600" />}
            iconBg="bg-blue-100"
            label="Running"
            value={runningCount}
            valueColor="text-blue-600"
          />
          <StatCard
            icon={<CheckCircle2 className="w-5 h-5 text-green-600" />}
            iconBg="bg-green-100"
            label="Completed"
            value={completedCount}
            valueColor="text-green-600"
          />
          <StatCard
            icon={<AlertTriangle className="w-5 h-5 text-red-600" />}
            iconBg="bg-red-100"
            label="Failed"
            value={failedCount}
            valueColor="text-red-600"
          />
        </div>

        {/* Pipeline Stages */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="p-4 border-b border-gray-200 flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-gray-900">Pipeline Stages</h3>
              <p className="text-sm text-gray-500 mt-1">{STAGES.length} source ingestion pipelines</p>
            </div>
            <button
              onClick={handleClearAll}
              disabled={jobCount === 0}
              className="inline-flex items-center gap-2 px-3 py-1.5 text-sm text-red-600 hover:text-red-700 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title="Clear all job records"
            >
              <Trash2 className="w-4 h-4" />
              Reset ({jobCount})
            </button>
          </div>

          <div className="divide-y divide-gray-100">
            {STAGES.map((stage, index) => {
              const Icon = stage.icon
              const job = jobs[stage.sourceKey]
              const isActive = job?.status === 'Running'
              const isExpanded = expandedStage === stage.id
              const count = sourceCounts[stage.sourceKey] || 0


              return (
                <div key={stage.id} className={`transition-colors ${isActive ? 'bg-blue-50/50' : ''}`}>
                  {/* Main Row */}
                  <div
                    className="p-4 cursor-pointer hover:bg-gray-50"
                    onClick={() => setExpandedStage(prev => prev === stage.id ? null : stage.id)}
                  >
                    <div className="flex items-center gap-3">
                      {/* Expand chevron */}
                      <div className="w-5 shrink-0">
                        {isExpanded ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
                      </div>

                      {/* Stage number */}
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center font-semibold text-sm shrink-0 ${
                        isActive
                          ? 'bg-blue-100 text-blue-600'
                          : job?.status === 'Failed'
                            ? 'bg-red-100 text-red-600'
                            : job?.status === 'Stopped'
                              ? 'bg-amber-100 text-amber-600'
                              : 'bg-indigo-100 text-indigo-600'
                      }`}>
                        {index + 1}
                      </div>

                      {/* Title & description */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <Icon className="w-4 h-4 text-gray-400 shrink-0" />
                          <h4 className="font-medium text-gray-900">{stage.name}</h4>
                          <StatusBadge status={job ? job.status : 'never_run'} />
                        </div>
                        <p className="text-sm text-start text-gray-500 mt-0.5">{stage.description}</p>
                      </div>

                      {/* Stats pills */}
                      <div className="hidden sm:flex items-center gap-3 shrink-0">
                        {count > 0 && (
                          <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">
                            {count.toLocaleString()} items
                          </span>
                        )}
                        {job?.items_ingested != null && job.items_ingested > 0 && (
                          <span className="text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full">
                            {job.items_ingested} ingested
                          </span>
                        )}
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-1.5 shrink-0" onClick={e => e.stopPropagation()}>
                        {(job?.status === 'Completed' || job?.status === 'Failed' || job?.status === 'Stopped') && (
                          <button
                            onClick={() => { downloadReport(generateJobReport(job, stage, count), job.source) }}
                            className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
                            title="Download report"
                          >
                            <Download className="w-4 h-4" />
                          </button>
                        )}
                        {(job?.status === 'Failed' || job?.status === 'Stopped') && (
                          <button
                            onClick={() => handleTrigger(stage.sourceKey)}
                            disabled={triggeringStage !== null || stoppingStage !== null}
                            className="p-1.5 text-gray-400 hover:text-amber-600 hover:bg-amber-50 rounded-lg transition-colors disabled:opacity-50"
                            title="Retry"
                          >
                            <RotateCcw className="w-4 h-4" />
                          </button>
                        )}
                        {/* Stop button — only visible when Running */}
                        {job?.status === 'Running' && (
                          <button
                            onClick={() => handleStop(stage.sourceKey)}
                            disabled={stoppingStage !== null || triggeringStage !== null}
                            className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50"
                            title="Stop ingestion"
                          >
                            {stoppingStage === stage.sourceKey ? (
                              <Loader2 className="w-4 h-4 animate-spin text-red-600" />
                            ) : (
                              <Square className="w-4 h-4" />
                            )}
                          </button>
                        )}
                        <button
                          onClick={() => handleTrigger(stage.sourceKey)}
                          disabled={job?.status === 'Running' || triggeringStage !== null || stoppingStage !== null}
                          className="p-1.5 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-lg transition-colors disabled:opacity-50"
                          title="Run this source"
                        >
                          {triggeringStage === stage.sourceKey ? (
                            <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
                          ) : (
                            <Play className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Expanded Steps */}
                  {isExpanded && (
                    <div className="px-4 pb-4 ml-13 space-y-3">
                      {/* Timing info */}
                      {job && (
                        <div className="flex flex-wrap gap-4 text-xs text-gray-500">
                          {job.started_at && (
                            <span className="flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              Started: {new Date(job.started_at).toLocaleString()}
                            </span>
                          )}
                          {job.completed_at && (
                            <span className="flex items-center gap-1">
                              <CheckCircle2 className="w-3 h-3" />
                              Completed: {new Date(job.completed_at).toLocaleString()}
                            </span>
                          )}
                          {job.started_at && job.completed_at && (
                            <span>
                              Duration: {Math.round((new Date(job.completed_at).getTime() - new Date(job.started_at).getTime()) / 1000)}s
                            </span>
                          )}
                        </div>
                      )}

                      {/* Pipeline Steps */}
                      <div className="border border-gray-200 rounded-lg overflow-hidden">
                        {stage.steps.map((step, stepIdx) => {
                          // Compute step status from job state.
                          // 3 steps: 0=Fetch & Parse, 1=OCR & Store, 2=Write to DB
                          // current_step tells us which individual step was triggered (null=full pipeline)
                          const currentStep = job?.current_step ?? null
                          const stepBackend = STEP_BACKEND_MAP[stepIdx]
                          const isDisabledStep = step.disabled === true
                          let stepStatus: 'completed' | 'running' | 'failed' | 'pending' = 'pending'
                          if (isDisabledStep) {
                            // Disabled steps (e.g., OCR for text-only sources) always show as pending/grayed
                            stepStatus = 'pending'
                          } else if (!job) {
                            stepStatus = 'pending'
                          } else if (job.status === 'Completed') {
                            // Individual step completed: only mark that step done
                            if (currentStep) {
                              stepStatus = stepBackend === currentStep ? 'completed' : 'pending'
                            } else {
                              // Full pipeline completed: mark all non-disabled steps as completed
                              stepStatus = 'completed'
                            }
                          } else if (job.status === 'Running') {
                            if (currentStep) {
                              // Individual step running: only that step shows as running
                              stepStatus = stepBackend === currentStep ? 'running' : 'pending'
                            } else if (!stage.requiresOcr) {
                              // Full pipeline, no OCR: fetch(0) → publish(2)
                              // Step 0 = running until publish starts, step 2 = pending until fetch done
                              if (stepIdx === 0) {
                                stepStatus = job.items_ingested > 0 ? 'completed' : 'running'
                              } else if (stepIdx === 2) {
                                stepStatus = job.items_ingested > 0 ? 'running' : 'pending'
                              }
                            } else {
                              // Full pipeline with OCR: infer from ocr_status
                              if (stepIdx === 1 && job.ocr_status === 'running') {
                                stepStatus = 'running'
                              } else if (stepIdx === 0 && (job.ocr_status === 'running' || job.ocr_status === 'completed')) {
                                stepStatus = 'completed'
                              } else if (stepIdx === 0) {
                                stepStatus = 'running'
                              } else {
                                stepStatus = 'pending'
                              }
                            }
                          } else if (job.status === 'Failed') {
                            if (currentStep) {
                              // Individual step failed: only that step shows as failed
                              stepStatus = stepBackend === currentStep ? 'failed' : 'pending'
                            } else {
                              const dfCallFailed =
                                typeof job.error_message === 'string' &&
                                (job.error_message.includes('503') ||
                                  job.error_message.includes('temporarily unavailable') ||
                                  job.error_message.includes('Data Foundations') ||
                                  job.error_message.includes('unreachable') ||
                                  job.error_message.includes('timed out') ||
                                  job.error_message.includes('timeout') ||
                                  job.error_message.includes('RequestTimeout'))
                              if (dfCallFailed) {
                                stepStatus = stepIdx === 0 ? 'failed' : 'pending'
                              } else if (stage.requiresOcr && job.ocr_status === 'failed') {
                                stepStatus = stepIdx === 0 ? 'completed' : stepIdx === 1 ? 'failed' : 'pending'
                              } else {
                                // Failed at last active step (publish)
                                const lastStep = stage.steps.length - 1
                                stepStatus = stepIdx < lastStep ? (step.disabled ? 'pending' : 'completed') : stepIdx === lastStep ? 'failed' : 'pending'
                              }
                            }
                          }

                          // OCR progress annotation
                          const showOcrProgress =
                            stepIdx === 1 &&
                            job?.ocr_status === 'running' &&
                            job.ocr_items_total > 0

                          // Show play button only on the first UI step that maps to each backend step
                          // (avoids duplicate buttons for steps 0+1 which both map to "fetch")
                          const backendStep = getBackendStep(stage.sourceKey, stepIdx)
                          const isFirstForBackend = stepIdx === 0 ||
                            getBackendStep(stage.sourceKey, stepIdx - 1) !== backendStep
                          const stepKey = `${stage.sourceKey}:${stepIdx}`
                          const isStepTriggering = triggeringStep === stepKey
                          const anyBusy = triggeringStage !== null || triggeringStep !== null || stoppingStage !== null
                          const isRunning = job?.status === 'Running'
                          const isDisabled = step.disabled === true

                          return (
                            <div
                              key={stepIdx}
                              className={`flex items-center gap-3 px-3 py-2.5 text-sm ${
                                stepIdx > 0 ? 'border-t border-gray-100' : ''
                              } ${stepStatus === 'running' ? 'bg-blue-50/50' : ''} ${isDisabled ? 'opacity-40' : ''}`}
                            >
                              <div className="shrink-0">
                                {isDisabled ? (
                                  <div className="w-4 h-4 rounded-full border-2 border-gray-200 bg-gray-100" />
                                ) : stepStatus === 'completed' ? <CheckCircle2 className="w-4 h-4 text-green-500" /> :
                                stepStatus === 'running' ? <Loader2 className="w-4 h-4 text-blue-500 animate-spin" /> :
                                stepStatus === 'failed' ? <XCircle className="w-4 h-4 text-red-500" /> : (
                                  <div className="w-4 h-4 rounded-full border-2 border-gray-300" />
                                )}
                              </div>
                              <div className="flex-1 min-w-0 flex items-baseline gap-2">
                                <span className={`font-medium whitespace-nowrap ${
                                  stepStatus === 'completed' ? 'text-gray-900' :
                                  stepStatus === 'running' ? 'text-blue-700' :
                                  stepStatus === 'failed' ? 'text-red-700' :
                                  'text-gray-400'
                                }`}>{step.label}</span>
                                <span className="text-xs text-gray-400 truncate">{step.description}</span>
                                {showOcrProgress && (
                                  <span className="text-xs text-blue-500 whitespace-nowrap">
                                    {job!.ocr_items_processed}/{job!.ocr_items_total} items
                                  </span>
                                )}
                              </div>
                              {/* Per-step play button */}
                              {isFirstForBackend && !isDisabled && (
                                <button
                                  onClick={() => handleStepTrigger(stage.sourceKey, stepIdx)}
                                  disabled={isRunning || anyBusy}
                                  className="p-1 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
                                  title={`Run: ${step.label}`}
                                >
                                  {isStepTriggering ? (
                                    <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-600" />
                                  ) : (
                                    <Play className="w-3.5 h-3.5" />
                                  )}
                                </button>
                              )}
                            </div>
                          )
                        })}
                      </div>

                      {/* Error */}
                      {job?.error_message && (
                        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex gap-2">
                          <AlertTriangle className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
                          <div>
                            <p className="text-xs font-semibold text-red-700 mb-0.5">Error</p>
                            <p className="text-xs text-red-600">{friendlyError(job.error_message)}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

export default PublicIngestionPage
