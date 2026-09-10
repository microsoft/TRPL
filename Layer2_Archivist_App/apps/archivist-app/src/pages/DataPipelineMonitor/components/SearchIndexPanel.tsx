import { useState, useEffect, useCallback, useRef } from 'react'
import { Search, RefreshCw, Info, Lock, CheckCircle2, XCircle } from 'lucide-react'
import { apiService } from '@/services/api'

interface RestoreJobStatus {
  has_job?: boolean
  job_id?: string
  status?: 'running' | 'completed' | 'failed' | string
  total_documents?: number
  processed_documents?: number
  started_at?: string
  started_by?: string
  updated_at?: string
  completed_at?: string
  error_message?: string
}

interface SearchIndexPanelProps {
  canEdit: boolean
  onToast: (toast: { type: 'success' | 'error' | 'info' | 'warning'; message: string }) => void
}

export default function SearchIndexPanel({ canEdit, onToast }: SearchIndexPanelProps) {
  const [isRestoring, setIsRestoring] = useState(false)
  const [showTooltip, setShowTooltip] = useState(false)
  const [jobStatus, setJobStatus] = useState<RestoreJobStatus | null>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  // Fetch job status
  const fetchStatus = useCallback(async () => {
    try {
      const status = await apiService.getSearchIndexRestoreStatus()
      setJobStatus(status)
      
      // If job is running, keep the restoring state
      if (status.has_job && status.status === 'running') {
        setIsRestoring(true)
      } else {
        setIsRestoring(false)
      }
    } catch (error) {
      console.error('Failed to fetch restore status:', error)
    }
  }, [])

  // Poll for status when restoring
  useEffect(() => {
    // Initial fetch
    fetchStatus()

    // Poll every 5 seconds if restoring
    let interval: NodeJS.Timeout | null = null
    if (isRestoring) {
      interval = setInterval(fetchStatus, 5000)
    }

    return () => {
      if (interval) clearInterval(interval)
    }
  }, [isRestoring, fetchStatus])

  const handleRestoreIndex = async () => {
    if (!canEdit) {
      onToast({ type: 'warning', message: 'Edit access required to restore search index' })
      return
    }

    // Check if already running
    if (jobStatus?.has_job && jobStatus.status === 'running') {
      onToast({ type: 'warning', message: 'A restore job is already running. Please wait for it to complete.' })
      return
    }

    setIsRestoring(true)

    try {
      const response = await apiService.restoreSearchIndex()
      onToast({ type: 'success', message: response.message || 'AI Search Index restore triggered successfully' })
      // Fetch status immediately
      fetchStatus()
    } catch (error) {
      console.error('Failed to restore search index:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to restore AI Search Index'
      
      // Check if it's a "already running" conflict
      if (errorMessage.includes('already running')) {
        onToast({ type: 'warning', message: errorMessage })
        // Refresh status to show current job
        fetchStatus()
      } else {
        onToast({ type: 'error', message: errorMessage })
        setIsRestoring(false)
      }
    }
  }

  // Determine status display
  const getStatusBadge = () => {
    if (!jobStatus?.has_job) return null

    switch (jobStatus.status) {
      case 'running':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-1 text-xs font-medium bg-blue-100 text-blue-700 rounded-full">
            <RefreshCw className="w-3 h-3 animate-spin" />
            Running
          </span>
        )
      case 'completed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-1 text-xs font-medium bg-green-100 text-green-700 rounded-full">
            <CheckCircle2 className="w-3 h-3" />
            Completed
          </span>
        )
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-1 text-xs font-medium bg-red-100 text-red-700 rounded-full">
            <XCircle className="w-3 h-3" />
            Failed
          </span>
        )
      default:
        return null
    }
  }

  return (
    <div className="flex items-center gap-3">
      {/* Button with info icon */}
      <div className="relative flex items-center gap-1.5">
        <button
          onClick={handleRestoreIndex}
          disabled={isRestoring || !canEdit}
          className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-all ${
            canEdit
              ? 'bg-violet-600 text-white hover:bg-violet-700'
              : 'bg-gray-100 text-gray-400 cursor-not-allowed'
          } disabled:opacity-60 disabled:cursor-not-allowed`}
          title={!canEdit ? 'Edit access required' : 'Restore AI Search Index from database'}
        >
          {isRestoring ? (
            <>
              <RefreshCw className="w-4 h-4 animate-spin" />
              Restoring...
            </>
          ) : (
            <>
              {!canEdit && <Lock className="w-4 h-4" />}
              <Search className="w-4 h-4" />
              Restore Search Index
            </>
          )}
        </button>

        {/* Info icon with hover tooltip */}
        <div 
          className="relative"
          onMouseEnter={() => setShowTooltip(true)}
          onMouseLeave={() => setShowTooltip(false)}
        >
          <button
            className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
            aria-label="More information"
          >
            <Info className="w-4 h-4" />
          </button>

          {/* Tooltip on hover */}
          {showTooltip && (
            <div 
              ref={tooltipRef}
              className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-72 p-3 bg-gray-900 text-white text-sm rounded-lg shadow-xl z-50"
            >
              <div className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-gray-900 rotate-45" />
              <p>
                Rebuilds the entire AI Search index by re-indexing all published documents from Cosmos DB. 
                This operation may take several minutes.
              </p>
              {jobStatus?.has_job && jobStatus.status === 'running' && (
                <p className="mt-2 pt-2 border-t border-gray-700 text-gray-300">
                  <strong className="text-white">Current job:</strong> {jobStatus.total_documents?.toLocaleString()} documents
                  {jobStatus.started_by && <span> by {jobStatus.started_by}</span>}
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Status badge */}
      {jobStatus?.has_job && (
        <div className="flex items-center gap-2">
          {getStatusBadge()}
          {jobStatus.status === 'running' && jobStatus.total_documents && (
            <span className="text-xs text-gray-500">
              {jobStatus.processed_documents?.toLocaleString() || 0}/{jobStatus.total_documents.toLocaleString()}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
