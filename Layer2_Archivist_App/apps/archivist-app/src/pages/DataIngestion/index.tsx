import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { 
  Database, 
  Search, 
  RefreshCw, 
  CheckCircle2, 
  XCircle, 
  Clock,
  AlertTriangle,
  Loader2,
  FileText,
  RotateCcw,
  Layers,
  User,
  Undo2,
  ChevronDown,
  Play,
  Pause,
  Square,
  ArrowRight,
  Lock,
  ExternalLink,
  History
} from 'lucide-react'
import { apiService } from '@/services/api'
import PublishBatchHistoryModal from './PublishBatchHistoryModal'
import PageHeader from '@/components/PageHeader'
import Toast from '@/components/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'
import { useAuth } from '@/contexts/AuthContext'

// =============================================================================
// Types
// =============================================================================

interface JobStatus {
  has_job?: boolean
  status?: string
  operation_type?: 'bulk' | 'retry_failed' | 'reindex'
  total_documents?: number
  processed_documents?: number
  success_documents?: number
  failed_documents?: number
  started_at?: string
  started_by?: string
  completed_at?: string
  error_message?: string
  status_url?: string
  instance_id?: string
  terminate_url?: string
  suspend_url?: string
  resume_url?: string
  published_by?: string
  published_date?: string
}

interface OrchestrationStatus {
  runtimeStatus: string
  input?: unknown
  output?: unknown
  customStatus?: unknown
  lastUpdatedTime?: string
}

interface ToastState {
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
}

interface ConfirmDialogState {
  isOpen: boolean
  title: string
  message: string
  confirmText: string
  variant: 'danger' | 'warning' | 'info'
  onConfirm: () => void
}

interface PublisherInfo {
  user: string
  dates: string[]
}

// =============================================================================
// Custom Hook
// =============================================================================

function useJobWithPolling(
  fetchFn: () => Promise<JobStatus | null>,
  updateFn: (data: { status: string; error_message?: string }) => Promise<unknown>,
  onComplete?: () => void
) {
  const [status, setStatus] = useState<JobStatus | null>(null)
  const [orchestration, setOrchestration] = useState<OrchestrationStatus | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const pollingRef = useRef<NodeJS.Timeout | null>(null)
  const backgroundRefreshRef = useRef<NodeJS.Timeout | null>(null)
  const statusUrlRef = useRef<string | undefined>(undefined)

  // Keep status URL in a ref to avoid recreating poll callback
  useEffect(() => {
    statusUrlRef.current = status?.status_url
  }, [status?.status_url])

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }, [])

  const stopBackgroundRefresh = useCallback(() => {
    if (backgroundRefreshRef.current) {
      clearInterval(backgroundRefreshRef.current)
      backgroundRefreshRef.current = null
    }
  }, [])

  const refresh = useCallback(async () => {
    setIsLoading(true)
    try {
      const result = await fetchFn()
      setStatus(result)
      return result
    } catch (error) {
      console.error('Failed to fetch job status:', error)
      return null
    } finally {
      setIsLoading(false)
    }
  }, [fetchFn])

  const poll = useCallback(async () => {
    const currentStatusUrl = statusUrlRef.current
    if (!currentStatusUrl) return
    try {
      const orchStatus = await apiService.getOrchestratorStatus(currentStatusUrl)
      if (orchStatus) {
        setOrchestration(orchStatus)
        
        // Check for terminal states and stop polling immediately
        const isTerminal = ['Completed', 'Failed', 'Terminated', 'Canceled'].includes(orchStatus.runtimeStatus)
        if (isTerminal) {
          stopPolling()
        }
        
        try {
          await updateFn({
            status: orchStatus.runtimeStatus,
            error_message: orchStatus.runtimeStatus === 'Failed' 
              ? (typeof orchStatus.output === 'string' ? orchStatus.output : 'Job failed') 
              : undefined
          })
        } catch (e) { console.error('Update failed:', e) }
        
        const updated = await fetchFn()
        setStatus(updated)
        
        if (isTerminal) {
          onComplete?.()
        }
      }
    } catch (e) { console.error('Poll failed:', e) }
  }, [fetchFn, updateFn, onComplete, stopPolling])

  // Initial fetch on mount
  useEffect(() => { refresh() }, [refresh])

  // Background refresh every 30 seconds (regardless of job state)
  useEffect(() => {
    backgroundRefreshRef.current = setInterval(refresh, 30000)
    return stopBackgroundRefresh
  }, [refresh, stopBackgroundRefresh])

  // Active job polling (every 10 seconds when job is running)
  useEffect(() => {
    const isActive = ['Running', 'Pending', 'Suspended'].includes(status?.status || '')
    
    // Clear any existing interval first
    stopPolling()
    
    if (isActive && status?.status_url) {
      // Do immediate poll then set up interval
      poll()
      pollingRef.current = setInterval(poll, 10000)
    } else {
      // Clear orchestration state when not active
      setOrchestration(null)
    }
    
    return stopPolling
  }, [status?.status, status?.status_url, poll, stopPolling])

  return { status, orchestration, isLoading, refresh, poll }
}

// =============================================================================
// Components
// =============================================================================

function StatusBadge({ status }: { status?: string }) {
  if (!status) return null
  
  const styles: Record<string, string> = {
    Running: 'bg-blue-600 text-white',
    Pending: 'bg-amber-500 text-white',
    Completed: 'bg-green-600 text-white',
    Failed: 'bg-red-600 text-white',
    Terminated: 'bg-gray-500 text-white',
    Suspended: 'bg-orange-500 text-white',
    Canceled: 'bg-gray-500 text-white',
  }
  
  return (
    <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded text-xs font-semibold uppercase tracking-wide ${styles[status] || styles.Pending}`}>
      {(status === 'Running' || status === 'Pending') && (
        <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
      )}
      {status}
    </span>
  )
}

function ProgressSection({ job }: { job: JobStatus }) {
  const total = job.total_documents || 0
  const processed = job.processed_documents || 0
  const success = job.success_documents || 0
  const failed = job.failed_documents || 0
  const percent = total > 0 ? Math.round((processed / total) * 100) : 0
  const successPct = total > 0 ? (success / total) * 100 : 0
  const failedPct = total > 0 ? (failed / total) * 100 : 0

  return (
    <div className="space-y-4">
      {/* Stats Row */}
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-1">
          <span className="text-3xl font-bold text-gray-900">{percent}</span>
          <span className="text-lg text-gray-400">%</span>
        </div>
        <span className="text-sm text-gray-500">
          {processed.toLocaleString()} of {total.toLocaleString()} documents
        </span>
      </div>

      {/* Progress Bar */}
      <div className="h-3 bg-gray-100 rounded-full overflow-hidden">
        <div className="h-full flex transition-all duration-700">
          <div className="bg-green-500" style={{ width: `${successPct}%` }} />
          <div className="bg-red-500" style={{ width: `${failedPct}%` }} />
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-6 text-sm">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded bg-green-500" />
          <span className="text-gray-600">{success.toLocaleString()} successful</span>
        </div>
        {failed > 0 && (
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded bg-red-500" />
            <span className="text-gray-600">{failed.toLocaleString()} failed</span>
          </div>
        )}
      </div>
    </div>
  )
}

function StatBox({ label, value, variant = 'default' }: { 
  label: string
  value: string | number 
  variant?: 'default' | 'success' | 'error'
}) {
  const colors = {
    default: 'bg-gray-50 text-gray-900',
    success: 'bg-green-50 text-green-700',
    error: 'bg-red-50 text-red-700',
  }
  return (
    <div className={`px-4 py-3 rounded-lg ${colors[variant]}`}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">{label}</p>
      <p className="text-xl font-bold">{typeof value === 'number' ? value.toLocaleString() : value}</p>
    </div>
  )
}

function UnpublishJobDescription({
  published_by,
  published_date,
  variant,
}: {
  published_by?: string
  published_date?: string
  variant: 'active' | 'completed'
}) {
  const pb = published_by || ''
  const pd = published_date || ''

  if (!pb) {
    return (
      <p className={variant === 'active' ? 'text-sm text-gray-600' : 'text-xs text-gray-500'}>
        {variant === 'active' ? 'Unpublishing documents…' : 'Previous unpublish job'}
      </p>
    )
  }

  if (pb.startsWith('publish-batch:')) {
    const batchId = pb.slice('publish-batch:'.length)
    if (variant === 'active') {
      return (
        <p className="text-sm text-gray-600">
          Unpublishing documents from publish batch{' '}
          <span className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded text-gray-800">{batchId}</span>
        </p>
      )
    }
    return (
      <p className="text-xs text-gray-500">
        Unpublished documents from publish batch{' '}
        <span className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">{batchId}</span>
      </p>
    )
  }

  if (pb === '(collection filters)' && pd === 'bulk-by-filters') {
    return (
      <p className={variant === 'active' ? 'text-sm text-gray-600' : 'text-xs text-gray-500'}>
        {variant === 'active'
          ? 'Unpublishing published documents that match the applied collection filters.'
          : 'Unpublished published documents that matched the applied collection filters.'}
      </p>
    )
  }

  const hideDate = !pd || pd === 'batch' || pd === 'bulk-by-filters'

  if (variant === 'active') {
    return (
      <p className="text-sm text-gray-600">
        Unpublishing documents published by <strong>{pb}</strong>
        {!hideDate && (
          <>
            {' '}
            on <strong>{pd}</strong>
          </>
        )}
      </p>
    )
  }
  return (
    <p className="text-xs text-gray-500">
      Unpublished documents published by {pb}
      {!hideDate && <> on {pd}</>}
    </p>
  )
}

// =============================================================================
// Main Component
// =============================================================================

export default function DataIngestionPage() {
  const navigate = useNavigate()
  const { permissions, isAdmin, user } = useAuth()
  const canEdit = permissions.dataIngestion.canEdit
  const [showUnpublish] = useState(true)
  
  // Check if current user can control a job (admin can control any, others only their own)
  const canControlJob = (startedBy?: string): boolean => {
    if (!canEdit) return false
    if (isAdmin) return true
    // Non-admin can only control their own jobs
    if (!startedBy || !user?.email) return false
    return startedBy.toLowerCase() === user.email.toLowerCase()
  }
  
  const [toast, setToast] = useState<ToastState | null>(null)
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState | null>(null)
  const [failedCount, setFailedCount] = useState(0)
  
  const [publishers, setPublishers] = useState<PublisherInfo[]>([])
  const [selectedPublisher, setSelectedPublisher] = useState('')
  const [selectedDate, setSelectedDate] = useState('')
  const [availableDates, setAvailableDates] = useState<string[]>([])
  const [singleDocumentId, setSingleDocumentId] = useState('')
  const [isSingleUnpublishing, setIsSingleUnpublishing] = useState(false)
  const [showPublishBatchHistory, setShowPublishBatchHistory] = useState(false)
  const unpublishSectionRef = useRef<HTMLDivElement>(null)

  const fetchIngestion = useCallback(() => apiService.getBulkIngestionStatus(), [])
  const fetchUnpublish = useCallback(() => apiService.getUnpublishStatus() as Promise<JobStatus>, [])
  const updateIngestion = useCallback((d: { status: string; error_message?: string }) => apiService.updateBulkIngestionStatus(d), [])
  const updateUnpublish = useCallback((d: { status: string; error_message?: string }) => apiService.updateUnpublishStatus(d), [])

  const fetchPublishers = useCallback(async () => {
    try {
      const r = await apiService.getPublishersList()
      setPublishers(r.publishers || [])
    } catch (e) { console.error(e) }
  }, [])

  const fetchFailedCount = useCallback(async () => {
    try {
      const r = await apiService.getFailedDocumentsCount()
      setFailedCount(r.failed_count)
    } catch (e) { console.error(e) }
  }, [])

  const ingestion = useJobWithPolling(fetchIngestion, updateIngestion, fetchFailedCount)
  const unpublish = useJobWithPolling(fetchUnpublish, updateUnpublish, fetchPublishers)

  useEffect(() => { fetchFailedCount(); fetchPublishers() }, [fetchFailedCount, fetchPublishers])
  useEffect(() => {
    if (['Completed', 'Failed'].includes(ingestion.status?.status || '')) fetchFailedCount()
  }, [ingestion.status?.status, fetchFailedCount])
  useEffect(() => {
    if (selectedPublisher) {
      const p = publishers.find(x => x.user === selectedPublisher)
      setAvailableDates(p?.dates || [])
      setSelectedDate('')
    } else {
      setAvailableDates([])
      setSelectedDate('')
    }
  }, [selectedPublisher, publishers])

  const closeDialog = () => setConfirmDialog(null)
  const showToast = (type: ToastState['type'], message: string) => setToast({ type, message })
  const showConfirm = (title: string, message: string, confirmText: string, variant: ConfirmDialogState['variant'], onConfirm: () => void) => {
    setConfirmDialog({ isOpen: true, title, message, confirmText, variant, onConfirm })
  }

  const effectiveStatus = ingestion.orchestration?.runtimeStatus || ingestion.status?.status
  const isJobActive = ['Running', 'Pending', 'Suspended'].includes(effectiveStatus || '')
  const isRunning = effectiveStatus === 'Running' || effectiveStatus === 'Pending'
  const isSuspended = effectiveStatus === 'Suspended'
  
  const unpublishStatus = unpublish.orchestration?.runtimeStatus || unpublish.status?.status
  const unpublishIsActive = ['Running', 'Pending', 'Suspended'].includes(unpublishStatus || '')

  const opConfig: Record<string, { label: string; icon: typeof Search }> = {
    reindex: { label: 'Search Index Restore', icon: Search },
    retry_failed: { label: 'Retry Failed', icon: RotateCcw },
    bulk: { label: 'Bulk Ingestion', icon: Database },
  }
  const op = opConfig[ingestion.status?.operation_type || 'bulk'] || opConfig.bulk

  // Handlers
  const handleTerminate = (job: 'ingestion' | 'unpublish') => {
    const t = job === 'ingestion' ? ingestion : unpublish
    if (!t.status?.terminate_url) return
    showConfirm('Stop Job', 'This will terminate the job. This action cannot be undone.', 'Stop Job', 'danger', async () => {
      closeDialog()
      try {
        await apiService.terminateOrchestration(
          t.status!.terminate_url!,
          'User terminated',
        )
        showToast('success', 'Job stopped')
        t.refresh()
        if (job === 'unpublish') fetchPublishers()
      } catch (e) { showToast('error', e instanceof Error ? e.message : 'Failed') }
    })
  }

  const handleSuspend = async (job: 'ingestion' | 'unpublish') => {
    const t = job === 'ingestion' ? ingestion : unpublish
    if (!t.status?.suspend_url) return
    try {
      await apiService.suspendOrchestration(
        t.status!.suspend_url!,
        'User paused',
      )
      showToast('success', 'Job paused')
      // Immediately refresh to get updated status
      await t.refresh()
      t.poll()
    } catch (e) { showToast('error', e instanceof Error ? e.message : 'Failed') }
  }

  const handleResume = async (job: 'ingestion' | 'unpublish') => {
    const t = job === 'ingestion' ? ingestion : unpublish
    if (!t.status?.resume_url) return
    try {
      await apiService.resumeOrchestration(
        t.status!.resume_url!,
        'User resumed',
      )
      showToast('success', 'Job resumed')
      // Immediately refresh to get updated status
      await t.refresh()
      t.poll()
    } catch (e) { showToast('error', e instanceof Error ? e.message : 'Failed') }
  }

  const handleReindex = () => {
    showConfirm('Restore Search Index', 'This will re-index all published documents into AI Search.', 'Start Restore', 'warning', async () => {
      closeDialog()
      try {
        const r = await apiService.restoreSearchIndex()
        showToast('success', r.message || 'Started')
        ingestion.refresh()
      } catch (e) { showToast('error', e instanceof Error ? e.message : 'Failed') }
    })
  }

  const handleRetry = async () => {
    if (failedCount === 0) return
    try {
      const r = await apiService.retryFailedDocuments()
      showToast('success', r.message || 'Started')
      ingestion.refresh()
      fetchFailedCount()
    } catch (e) { showToast('error', e instanceof Error ? e.message : 'Failed') }
  }

  const handleBulkUnpublish = () => {
    if (!selectedPublisher || !selectedDate) return
    showConfirm('Unpublish Documents', `This will unpublish all documents by "${selectedPublisher}" on ${selectedDate}.`, 'Unpublish', 'warning', async () => {
      closeDialog()
      try {
        const r = await apiService.unpublishDocuments(selectedPublisher, selectedDate)
        showToast('success', r.message || 'Started')
        unpublish.refresh()
        setSelectedPublisher('')
        setSelectedDate('')
      } catch (e) { showToast('error', e instanceof Error ? e.message : 'Failed') }
    })
  }

  const handleSingleUnpublish = () => {
    const id = singleDocumentId.trim()
    if (!id) return
    showConfirm('Unpublish Document', `This will unpublish document "${id}".`, 'Unpublish', 'warning', async () => {
      closeDialog()
      setIsSingleUnpublishing(true)
      try {
        const r = await apiService.unpublishSingleDocument(id)
        showToast('success', r.message || 'Done')
        setSingleDocumentId('')
        fetchPublishers()
      } catch (e) { showToast('error', e instanceof Error ? e.message : 'Failed') }
      finally { setIsSingleUnpublishing(false) }
    })
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <PageHeader
        title="Data Ingestion"
        subtitle="Manage document processing and search index operations"
      />

      {/* Read-only notice */}
      {!canEdit && (
        <div className="max-w-7xl mx-auto px-6 pt-6">
          <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
            <Lock className="w-4 h-4 flex-shrink-0" />
            <span>You have read-only access to this page. Contact an administrator for edit permissions.</span>
          </div>
        </div>
      )}

      <div className="max-w-7xl mx-auto px-6 py-8 space-y-6">

        {/* Document Ingestion */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          {/* Header */}
          <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-blue-600 flex items-center justify-center">
                <Database className="w-5 h-5 text-white" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Document Ingestion</h2>
                <p className="text-sm text-gray-500">Process documents into AI Search</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {ingestion.status?.has_job && <StatusBadge status={effectiveStatus} />}
              <button
                type="button"
                onClick={() => setShowPublishBatchHistory(true)}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-700 bg-gray-100 border border-gray-200 rounded-lg hover:bg-gray-200 transition-colors"
              >
                <History className="w-4 h-4" />
                Publish batch history
              </button>
              <button
                onClick={() => { ingestion.refresh(); fetchFailedCount() }}
                disabled={ingestion.isLoading}
                className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <RefreshCw className={`w-5 h-5 ${ingestion.isLoading ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>

          {/* Body */}
          <div className="p-6">
            {ingestion.status?.has_job ? (
              <div className="space-y-6">
                {/* Operation Info */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm">
                    <op.icon className="w-4 h-4 text-blue-600" />
                    <span className="font-medium text-gray-900">{op.label}</span>
                    {ingestion.status.started_by && (
                      <span className="text-gray-400">by {ingestion.status.started_by}</span>
                    )}
                  </div>
                  {ingestion.status.started_at && (
                    <span className="text-sm text-gray-400">
                      <Clock className="w-4 h-4 inline mr-1" />
                      {new Date(ingestion.status.started_at).toLocaleString()}
                    </span>
                  )}
                </div>

                {/* Progress (when running) */}
                {(isRunning || isSuspended) && <ProgressSection job={ingestion.status} />}

                {/* Final State (when not running) */}
                {!isRunning && !isSuspended && effectiveStatus && (
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                    <StatBox label="Total" value={ingestion.status.total_documents || 0} />
                    <StatBox label="Successful" value={ingestion.status.success_documents || 0} variant="success" />
                    {(ingestion.status.failed_documents || 0) > 0 && (
                      <StatBox label="Failed" value={ingestion.status.failed_documents || 0} variant="error" />
                    )}
                  </div>
                )}

                {/* Error */}
                {ingestion.status.error_message && (
                  <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex gap-3">
                    <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0" />
                    <p className="text-sm text-red-700">{ingestion.status.error_message}</p>
                  </div>
                )}

                {/* Controls - only show if user can control this job */}
                {(isRunning || isSuspended) && (
                  <div className="flex gap-2 pt-4 border-t border-gray-100">
                    {isRunning && ingestion.status?.suspend_url && (
                      <button 
                        onClick={() => handleSuspend('ingestion')} 
                        disabled={!canControlJob(ingestion.status?.started_by)}
                        title={!canControlJob(ingestion.status?.started_by) ? (isAdmin ? 'Edit access required' : 'Only the user who started this job or an admin can control it') : undefined}
                        className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <Pause className="w-4 h-4" /> Pause
                      </button>
                    )}
                    {isSuspended && ingestion.status?.resume_url && (
                      <button 
                        onClick={() => handleResume('ingestion')} 
                        disabled={!canControlJob(ingestion.status?.started_by)}
                        title={!canControlJob(ingestion.status?.started_by) ? (isAdmin ? 'Edit access required' : 'Only the user who started this job or an admin can control it') : undefined}
                        className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <Play className="w-4 h-4" /> Resume
                      </button>
                    )}
                    {ingestion.status?.terminate_url && (
                      <button 
                        onClick={() => handleTerminate('ingestion')} 
                        disabled={!canControlJob(ingestion.status?.started_by)}
                        title={!canControlJob(ingestion.status?.started_by) ? (isAdmin ? 'Edit access required' : 'Only the user who started this job or an admin can control it') : undefined}
                        className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-red-700 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <Square className="w-4 h-4" /> Stop
                      </button>
                    )}
                  </div>
                )}
              </div>
            ) : (
              /* No Job State */
              <div className="text-center py-4">
                <p className="text-gray-500">No active job</p>
              </div>
            )}

            {/* Action Buttons - Always visible, grayed out when job is running */}
            <div className="pt-6 border-t border-gray-100 mt-6">
              <div className="grid sm:grid-cols-2 gap-4">
                {/* Restore Index */}
                <button
                  onClick={handleReindex}
                  disabled={!canEdit || isRunning || isSuspended}
                  title={!canEdit ? 'Edit access required' : (isRunning || isSuspended) ? 'Wait for current job to complete' : undefined}
                  className="group flex items-center gap-4 p-4 text-left bg-white border-2 border-gray-200 rounded-xl hover:border-blue-500 hover:bg-blue-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-gray-200 disabled:hover:bg-white"
                >
                  <div className="w-12 h-12 rounded-xl bg-green-100 flex items-center justify-center group-hover:bg-green-200 transition-colors group-disabled:group-hover:bg-green-100">
                    <Search className="w-6 h-6 text-green-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-gray-900">Restore Search Index</h3>
                    <p className="text-sm text-gray-500">Re-index all published documents</p>
                  </div>
                  <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors group-disabled:group-hover:text-gray-300" />
                </button>

                {/* Retry Failed */}
                <div className="space-y-2">
                  <button
                    onClick={handleRetry}
                    disabled={failedCount === 0 || !canEdit || isRunning || isSuspended}
                    title={!canEdit ? 'Edit access required' : (isRunning || isSuspended) ? 'Wait for current job to complete' : undefined}
                    className="w-full group flex items-center gap-4 p-4 text-left bg-white border-2 border-gray-200 rounded-xl hover:border-blue-500 hover:bg-blue-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-gray-200 disabled:hover:bg-white"
                  >
                    <div className="w-12 h-12 rounded-xl bg-amber-100 flex items-center justify-center group-hover:bg-amber-200 transition-colors group-disabled:group-hover:bg-amber-100">
                      <RotateCcw className="w-6 h-6 text-amber-600" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <h3 className="font-semibold text-gray-900">Retry Failed</h3>
                      <p className="text-sm text-gray-500">
                        {failedCount > 0 ? (
                          <span className="text-amber-600 font-medium">{failedCount} document{failedCount !== 1 ? 's' : ''}</span>
                        ) : (
                          'No failed documents'
                        )}
                      </p>
                    </div>
                    <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors group-disabled:group-hover:text-gray-300" />
                  </button>
                  {/* View Errors Link */}
                  {failedCount > 0 && (
                    <button
                      onClick={() => navigate('/tools/data-ingestion/errors')}
                      className="w-full flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-red-600 hover:text-red-700 hover:bg-red-50 rounded-lg transition-colors"
                    >
                      <AlertTriangle className="w-4 h-4" />
                      View Error Details
                      <ExternalLink className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>

              <p className="text-xs text-center text-gray-400 mt-4">
                Bulk ingestion is triggered from the document approval screen
              </p>
            </div>
          </div>
        </div>

        {/* Unpublish Documents - shown when feature flag is enabled, admin only */}
        {showUnpublish && (
        <div
          id="data-ingestion-unpublish"
          ref={unpublishSectionRef}
          className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden scroll-mt-4"
        >
          {/* Admin-only notice */}
          {!isAdmin && (
            <div className="px-6 py-3 bg-amber-50 border-b border-amber-100 flex items-center gap-2 text-sm text-amber-700">
              <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              <span>Only administrators can unpublish documents.</span>
            </div>
          )}
          {/* Header */}
          <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-red-500 flex items-center justify-center">
                <Undo2 className="w-5 h-5 text-white" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Unpublish Documents</h2>
                <p className="text-sm text-gray-500">Remove documents from AI Search</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {unpublish.status?.has_job && <StatusBadge status={unpublishStatus} />}
              <button
                onClick={() => { unpublish.refresh(); fetchPublishers() }}
                disabled={unpublish.isLoading}
                className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <RefreshCw className={`w-5 h-5 ${unpublish.isLoading ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>

          {/* Body */}
          <div className="p-6">
            {unpublish.status?.has_job && unpublishIsActive ? (
              <div className="space-y-6">
                <ProgressSection job={unpublish.status} />

                <UnpublishJobDescription
                  published_by={unpublish.status.published_by}
                  published_date={unpublish.status.published_date}
                  variant="active"
                />

                <div className="flex gap-2 pt-4 border-t border-gray-100">
                  {(unpublishStatus === 'Running' || unpublishStatus === 'Pending') && unpublish.status?.suspend_url && (
                    <button 
                      onClick={() => handleSuspend('unpublish')} 
                      disabled={!canControlJob(unpublish.status?.started_by)}
                      title={!canControlJob(unpublish.status?.started_by) ? (isAdmin ? 'Edit access required' : 'Only the user who started this job or an admin can control it') : undefined}
                      className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <Pause className="w-4 h-4" /> Pause
                    </button>
                  )}
                  {unpublishStatus === 'Suspended' && unpublish.status?.resume_url && (
                    <button 
                      onClick={() => handleResume('unpublish')} 
                      disabled={!canControlJob(unpublish.status?.started_by)}
                      title={!canControlJob(unpublish.status?.started_by) ? (isAdmin ? 'Edit access required' : 'Only the user who started this job or an admin can control it') : undefined}
                      className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <Play className="w-4 h-4" /> Resume
                    </button>
                  )}
                  {unpublish.status?.terminate_url && (
                    <button 
                      onClick={() => handleTerminate('unpublish')} 
                      disabled={!canControlJob(unpublish.status?.started_by)}
                      title={!canControlJob(unpublish.status?.started_by) ? (isAdmin ? 'Edit access required' : 'Only the user who started this job or an admin can control it') : undefined}
                      className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-red-700 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <Square className="w-4 h-4" /> Stop
                    </button>
                  )}
                </div>
              </div>
            ) : unpublish.status?.has_job && !unpublishIsActive && unpublishStatus ? (
              /* Completed unpublish job - show results and form for new job */
              <div className="space-y-6">
                {/* Previous job results */}
                <div className="p-4 bg-gray-50 rounded-lg space-y-4">
                  <span className="text-sm font-medium text-gray-700">Previous Job</span>
                  <div className="grid grid-cols-2 gap-4">
                    <StatBox label="Total" value={unpublish.status.total_documents || 0} />
                    <StatBox label="Successful" value={unpublish.status.success_documents || 0} variant="success" />
                  </div>
                  <UnpublishJobDescription
                    published_by={unpublish.status.published_by}
                    published_date={unpublish.status.published_date}
                    variant="completed"
                  />
                </div>

                {/* Form for new unpublish job */}
                <div className="pt-4 border-t border-gray-100">
                  <p className="text-sm text-gray-500 mb-4">Start a new unpublish operation:</p>
                  <div className="grid md:grid-cols-2 gap-6">
                    {/* Bulk Unpublish */}
                    <div className="space-y-3">
                      <div className="flex items-center gap-2">
                        <User className="w-4 h-4 text-gray-400" />
                        <h3 className="text-sm font-medium text-gray-900">By User & Date</h3>
                      </div>
                      {publishers.length === 0 ? (
                        <p className="text-sm text-gray-400">No published documents</p>
                      ) : (
                        <>
                          <div className="relative">
                            <select
                              value={selectedPublisher}
                              onChange={(e) => setSelectedPublisher(e.target.value)}
                              disabled={isJobActive || !isAdmin}
                              className="w-full px-3 py-2 pr-10 bg-white border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 appearance-none disabled:opacity-50 disabled:bg-gray-50"
                            >
                              <option value="">Select publisher...</option>
                              {publishers.map((p) => (
                                <option key={p.user} value={p.user}>{p.user}</option>
                              ))}
                            </select>
                            <ChevronDown className="w-4 h-4 text-gray-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                          </div>
                          {selectedPublisher && (
                            <div className="relative">
                              <select
                                value={selectedDate}
                                onChange={(e) => setSelectedDate(e.target.value)}
                                disabled={isJobActive || !isAdmin}
                                className="w-full px-3 py-2 pr-10 bg-white border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 appearance-none disabled:opacity-50 disabled:bg-gray-50"
                              >
                                <option value="">Select date...</option>
                                {availableDates.map((d) => (
                                  <option key={d} value={d}>{d}</option>
                                ))}
                              </select>
                              <ChevronDown className="w-4 h-4 text-gray-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                            </div>
                          )}
                          <button
                            onClick={handleBulkUnpublish}
                            disabled={!selectedPublisher || !selectedDate || isJobActive || !isAdmin}
                            title={!isAdmin ? 'Admin access required' : undefined}
                            className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-red-500 rounded-lg hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            <Undo2 className="w-4 h-4" /> Unpublish
                          </button>
                        </>
                      )}
                    </div>

                    {/* Single Document */}
                    <div className="space-y-3">
                      <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4 text-gray-400" />
                        <h3 className="text-sm font-medium text-gray-900">Single Document</h3>
                      </div>
                      <input
                        type="text"
                        value={singleDocumentId}
                        onChange={(e) => setSingleDocumentId(e.target.value)}
                        placeholder="Enter document ID..."
                        disabled={isJobActive || isSingleUnpublishing || !isAdmin}
                        className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-50 disabled:bg-gray-50"
                      />
                      <button
                        onClick={handleSingleUnpublish}
                        disabled={!singleDocumentId.trim() || isJobActive || isSingleUnpublishing || !isAdmin}
                        title={!isAdmin ? 'Admin access required' : undefined}
                        className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-red-600 bg-white border border-red-300 rounded-lg hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {isSingleUnpublishing ? (
                          <><Loader2 className="w-4 h-4 animate-spin" /> Processing...</>
                        ) : (
                          <><Undo2 className="w-4 h-4" /> Unpublish</>
                        )}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="grid md:grid-cols-2 gap-6">
                {/* Bulk Unpublish */}
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <User className="w-4 h-4 text-gray-400" />
                    <h3 className="font-medium text-gray-900">By User & Date</h3>
                  </div>

                  {publishers.length === 0 ? (
                    <p className="text-sm text-gray-400 py-4">No published documents</p>
                  ) : (
                    <div className="space-y-3">
                      <div className="relative">
                        <select
                          value={selectedPublisher}
                          onChange={(e) => setSelectedPublisher(e.target.value)}
                          disabled={unpublishIsActive || isJobActive || !isAdmin}
                          className="w-full px-3 py-2 pr-10 bg-white border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 appearance-none disabled:opacity-50 disabled:bg-gray-50"
                        >
                          <option value="">Select publisher...</option>
                          {publishers.map((p) => (
                            <option key={p.user} value={p.user}>{p.user}</option>
                          ))}
                        </select>
                        <ChevronDown className="w-4 h-4 text-gray-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                      </div>

                      {selectedPublisher && (
                        <div className="relative">
                          <select
                            value={selectedDate}
                            onChange={(e) => setSelectedDate(e.target.value)}
                            disabled={unpublishIsActive || isJobActive || !isAdmin}
                            className="w-full px-3 py-2 pr-10 bg-white border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 appearance-none disabled:opacity-50 disabled:bg-gray-50"
                          >
                            <option value="">Select date...</option>
                            {availableDates.map((d) => (
                              <option key={d} value={d}>{d}</option>
                            ))}
                          </select>
                          <ChevronDown className="w-4 h-4 text-gray-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                        </div>
                      )}

                      <button
                        onClick={handleBulkUnpublish}
                        disabled={!selectedPublisher || !selectedDate || unpublishIsActive || isJobActive || !isAdmin}
                        title={!isAdmin ? 'Admin access required' : undefined}
                        className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-red-500 rounded-lg hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <Undo2 className="w-4 h-4" /> Unpublish
                      </button>
                    </div>
                  )}
                </div>

                {/* Single Document */}
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <FileText className="w-4 h-4 text-gray-400" />
                    <h3 className="font-medium text-gray-900">Single Document</h3>
                  </div>

                  <div className="space-y-3">
                    <input
                      type="text"
                      value={singleDocumentId}
                      onChange={(e) => setSingleDocumentId(e.target.value)}
                      placeholder="Enter document ID..."
                      disabled={unpublishIsActive || isJobActive || isSingleUnpublishing || !isAdmin}
                      className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-50 disabled:bg-gray-50"
                    />
                    
                    <button
                      onClick={handleSingleUnpublish}
                      disabled={!singleDocumentId.trim() || unpublishIsActive || isJobActive || isSingleUnpublishing || !isAdmin}
                      title={!isAdmin ? 'Admin access required' : undefined}
                      className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-red-600 bg-white border border-red-300 rounded-lg hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {isSingleUnpublishing ? (
                        <><Loader2 className="w-4 h-4 animate-spin" /> Processing...</>
                      ) : (
                        <><Undo2 className="w-4 h-4" /> Unpublish</>
                      )}
                    </button>
                  </div>
                </div>

                {isJobActive && (
                  <div className="md:col-span-2 p-3 bg-amber-50 border border-amber-200 rounded-lg flex items-center gap-2 text-sm text-amber-700">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0" />
                    Wait for the current ingestion job to complete
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        )}

      </div>

      <PublishBatchHistoryModal
        isOpen={showPublishBatchHistory}
        onClose={() => setShowPublishBatchHistory(false)}
        canEdit={canEdit}
        onToast={(type, message) => setToast({ type, message })}
        onUnpublishJobStarted={() => {
          void unpublish.refresh()
          setShowPublishBatchHistory(false)
          requestAnimationFrame(() => {
            unpublishSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          })
        }}
      />

      {toast && <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />}

      {confirmDialog && (
        <ConfirmDialog
          isOpen={confirmDialog.isOpen}
          title={confirmDialog.title}
          message={confirmDialog.message}
          confirmText={confirmDialog.confirmText}
          cancelText="Cancel"
          variant={confirmDialog.variant}
          onConfirm={confirmDialog.onConfirm}
          onClose={closeDialog}
        />
      )}
    </div>
  )
}
