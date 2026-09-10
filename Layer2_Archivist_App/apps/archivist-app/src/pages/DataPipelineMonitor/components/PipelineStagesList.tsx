import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, CheckCircle2, XCircle, Loader2, PlayCircle, ChevronDown, ChevronRight, Trash2, ExternalLink } from 'lucide-react'
import StatusBadge from './StatusBadge'
import JobControls from './JobControls'
import type { PipelineStage, ActiveJob, PipelineStats } from '../types'

interface PipelineStagesListProps {
  stages: PipelineStage[]
  stats: PipelineStats | null
  activeJobs: ActiveJob[]
  triggeringStage: string | null
  managingJob: string | null
  isLoading?: boolean
  isResetting?: boolean
  canEdit?: boolean
  currentUserEmail?: string
  isAdmin?: boolean
  onTrigger: (stage: PipelineStage) => void
  onResumeJob: (job: ActiveJob) => void
  onSuspendJob: (job: ActiveJob) => void
  onTerminateJob: (job: ActiveJob) => void
  onResetAll: () => void
}

export default function PipelineStagesList({
  stages,
  stats,
  activeJobs,
  triggeringStage,
  managingJob,
  isLoading = false,
  isResetting = false,
  canEdit = false,
  currentUserEmail,
  isAdmin = false,
  onTrigger,
  onResumeJob,
  onSuspendJob,
  onTerminateJob,
  onResetAll
}: PipelineStagesListProps) {
  const navigate = useNavigate()
  const [expandedStage, setExpandedStage] = useState<string | null>(null)

  const getStageStats = (stageId: string) => {
    if (!stats?.by_stage) return null
    return stats.by_stage[stageId]
  }

  const getJobForStage = (stage: PipelineStage) => {
    // First try to find by trigger endpoint (most accurate)
    if (stage.triggerEndpoint) {
      const jobByEndpoint = activeJobs.find(job => job.triggerEndpoint === stage.triggerEndpoint)
      if (jobByEndpoint) return jobByEndpoint
    }
    
    // Fallback: find by name - first try active job (Pending/Running/Suspended)
    const activeJob = activeJobs.find(job => 
      job.name === stage.name && 
      ['Pending', 'Running', 'Suspended'].includes(job.runtimeStatus)
    )
    if (activeJob) return activeJob
    
    // Otherwise return the most recent job by name (Completed/Failed/Terminated)
    return activeJobs.find(job => job.name === stage.name)
  }
  
  const isJobActive = (job: ActiveJob) => {
    return ['Pending', 'Running', 'Suspended'].includes(job.runtimeStatus)
  }

  const toggleExpand = (stageId: string) => {
    setExpandedStage(prev => prev === stageId ? null : stageId)
  }

  const formatDateTime = (dateString: string) => {
    // Ensure UTC dates are properly parsed (add 'Z' if no timezone info)
    let normalizedDate = dateString
    if (!dateString.endsWith('Z') && !dateString.includes('+') && !/\d{2}-\d{2}:\d{2}$/.test(dateString)) {
      normalizedDate = dateString + 'Z'
    }
    const date = new Date(normalizedDate)
    return date.toLocaleString(undefined, { 
      month: 'short', 
      day: 'numeric',
      hour: '2-digit', 
      minute: '2-digit'
    })
  }

  if (isLoading) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200">
          <h3 className="font-semibold text-gray-900">Pipeline Stages</h3>
          <p className="text-sm text-gray-500 mt-1">Loading stages configuration...</p>
        </div>
        <div className="p-8 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
        </div>
      </div>
    )
  }

  if (stages.length === 0) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200">
          <h3 className="font-semibold text-gray-900">Pipeline Stages</h3>
          <p className="text-sm text-gray-500 mt-1">No stages configured</p>
        </div>
        <div className="p-8 text-center text-gray-500">
          Unable to load pipeline stages configuration
        </div>
      </div>
    )
  }

  // Count jobs that have records
  const jobCount = activeJobs.length

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-gray-900">Pipeline Stages</h3>
          <p className="text-sm text-gray-500 mt-1">{stages.length} sequential data processing stages</p>
        </div>
        
        {/* Reset All Jobs Button */}
        <button
          onClick={onResetAll}
          disabled={isResetting || jobCount === 0 || !canEdit}
          className="inline-flex items-center gap-2 px-3 py-1.5 text-sm text-red-600 hover:text-red-700 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          title={!canEdit ? 'Edit access required' : 'Reset all pipeline jobs - delete all job records from database'}
        >
          {isResetting ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Trash2 className="w-4 h-4" />
          )}
          {isResetting ? 'Resetting...' : `Reset (${jobCount})`}
        </button>
      </div>

      <div className="divide-y divide-gray-100">
        {stages.map((stage, index) => {
          const Icon = stage.icon
          const stageJob = getJobForStage(stage)
          const stageStats = getStageStats(stage.id)
          const isActive = stageJob && isJobActive(stageJob)
          const isExpanded = expandedStage === stage.id
          const isClickable = !!stageJob
          
          return (
            <div key={stage.id} className={`transition-colors ${isActive ? 'bg-blue-50/50' : ''}`}>
              {/* Main Row */}
              <div 
                className={`p-4 ${isClickable ? 'cursor-pointer hover:bg-gray-50' : ''}`}
                onClick={() => isClickable && toggleExpand(stage.id)}
              >
                {/* Top row: title, status, actions */}
                <div className="flex items-center gap-3">
                  {/* Expand indicator */}
                  <div className="w-5 flex-shrink-0">
                    {isClickable ? (
                      isExpanded ? (
                        <ChevronDown className="w-4 h-4 text-gray-400" />
                      ) : (
                        <ChevronRight className="w-4 h-4 text-gray-400" />
                      )
                    ) : <div className="w-4" />}
                  </div>
                  
                  {/* Step number - always show number, use color to indicate active */}
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center font-semibold text-sm flex-shrink-0 ${
                    isActive 
                      ? 'bg-blue-100 text-blue-600' 
                      : stageStats?.error && stageStats.error > 0
                        ? 'bg-red-100 text-red-600'
                        : 'bg-indigo-100 text-indigo-600'
                  }`}>
                    {index + 1}
                  </div>
                  
                  {/* Title and status */}
                  <div className="flex items-center gap-2 flex-wrap flex-1 min-w-0">
                    <Icon className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    <h4 className="font-medium text-gray-900">{stage.name}</h4>
                    
                    {stage.isAutomated && (
                      <span className="text-xs px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">Auto</span>
                    )}
                    
                    {/* Status tag */}
                    {stageJob && (
                      <StatusBadge status={stageJob.runtimeStatus} />
                    )}
                    
                    {/* Started/Stopped times */}
                    {stageJob && (
                      <span className="text-xs text-gray-400">
                        Started: {formatDateTime(stageJob.startedAt)}
                        {!isActive && stageJob.lastUpdatedTime && (
                          <> • Stopped: {formatDateTime(stageJob.lastUpdatedTime)}</>
                        )}
                      </span>
                    )}
                  </div>
                  
                  {/* Right side: stats and actions */}
                  <div className="flex items-center gap-3 flex-shrink-0">
                    {/* Stats badges */}
                    {stageStats && (
                      <div className="hidden sm:flex items-center gap-2 text-xs">
                        <span className="inline-flex items-center gap-1 px-2 py-1 bg-amber-50 text-amber-700 rounded-md" title="Pending">
                          <Clock className="w-3 h-3" />
                          {stageStats.pending.toLocaleString()}
                        </span>
                        <span className="inline-flex items-center gap-1 px-2 py-1 bg-green-50 text-green-700 rounded-md" title="Completed">
                          <CheckCircle2 className="w-3 h-3" />
                          {stageStats.completed.toLocaleString()}
                        </span>
                        {stageStats.error > 0 && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              navigate(`/tools/data-pipeline/errors/${stage.id}`)
                            }}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-md transition-colors cursor-pointer bg-red-50 text-red-700 hover:bg-red-100 hover:ring-1 hover:ring-red-300"
                            title="Click to view all errors"
                          >
                            <XCircle className="w-3 h-3" />
                            {stageStats.error.toLocaleString()}
                            <ExternalLink className="w-3 h-3 ml-0.5" />
                          </button>
                        )}
                      </div>
                    )}
                    
                    {/* Action buttons */}
                    {stage.isAutomated ? (
                      <span className="p-2 text-gray-300" title="Timer Triggered - Automatic">
                        <Clock className="w-5 h-5" />
                      </span>
                    ) : isActive && stageJob ? (
                      /* When running: show job controls */
                      <div onClick={e => e.stopPropagation()}>
                        <JobControls
                          job={stageJob}
                          managingJob={managingJob}
                          onResume={onResumeJob}
                          onSuspend={onSuspendJob}
                          onTerminate={onTerminateJob}
                          disabled={!canEdit}
                          currentUserEmail={currentUserEmail}
                          isAdmin={isAdmin}
                        />
                      </div>
                    ) : (
                      /* When not running: show play button */
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          onTrigger(stage)
                        }}
                        disabled={triggeringStage === stage.id || !canEdit}
                        className="p-2 rounded-lg transition-colors text-gray-400 hover:text-green-600 hover:bg-green-50 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-gray-400 disabled:hover:bg-transparent"
                        title={!canEdit ? 'Edit access required' : `Run ${stage.name}`}
                      >
                        {triggeringStage === stage.id ? (
                          <Loader2 className="w-5 h-5 animate-spin" />
                        ) : (
                          <PlayCircle className="w-5 h-5" />
                        )}
                      </button>
                    )}
                  </div>
                </div>
                
                {/* Description row - left aligned under the title */}
                {/* ml calculation: w-5 (20px) + gap-3 (12px) + w-8 (32px) + gap-3 (12px) = 76px ≈ 4.75rem */}
                <p className="text-sm text-gray-500 text-left mt-1" style={{ marginLeft: '76px' }}>{stage.description}</p>
              </div>
              
              {/* Expanded Current Stats - shows output for completed, input for in-progress */}
              {isExpanded && stageJob && (
                <div className="px-4 pb-4">
                  <div className="ml-16 p-3 bg-gray-50 border border-gray-200 rounded-lg">
                    {stageJob.currentStats && typeof stageJob.currentStats === 'object' ? (
                      <>
                        <div className="flex flex-wrap gap-3 text-xs">
                          {stageJob.currentStats.total !== undefined && (
                            <span className="px-2 py-1 bg-white border border-gray-200 text-gray-600 rounded">
                              Total: <strong>{stageJob.currentStats.total?.toLocaleString()}</strong>
                            </span>
                          )}
                          {stageJob.currentStats.total_processed_so_far !== undefined && (
                            <span className="px-2 py-1 bg-green-100 text-green-700 rounded">
                              Processed: <strong>{stageJob.currentStats.total_processed_so_far?.toLocaleString()}</strong>
                            </span>
                          )}
                          {stageJob.currentStats.total_failed_so_far !== undefined && stageJob.currentStats.total_failed_so_far > 0 && (
                            <span className="px-2 py-1 bg-red-100 text-red-700 rounded">
                              Failed: <strong>{stageJob.currentStats.total_failed_so_far?.toLocaleString()}</strong>
                            </span>
                          )}
                          {stageJob.currentStats.batch_size !== undefined && (
                            <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded">
                              Batch: <strong>{stageJob.currentStats.batch_size}</strong>
                            </span>
                          )}
                          {stageJob.currentStats.parallel_batches !== undefined && (
                            <span className="px-2 py-1 bg-indigo-100 text-indigo-700 rounded">
                              Parallel: <strong>{stageJob.currentStats.parallel_batches}</strong>
                            </span>
                          )}
                        </div>
                        
                        {/* Progress bar - shows processed (green) + failed (red) */}
                        {stageJob.currentStats.total && stageJob.currentStats.total_processed_so_far !== undefined && (
                          (() => {
                            const total = stageJob.currentStats.total || 0
                            const processed = stageJob.currentStats.total_processed_so_far || 0
                            const failed = stageJob.currentStats.total_failed_so_far || 0
                            const completed = processed + failed
                            const processedPct = total > 0 ? (processed / total) * 100 : 0
                            const failedPct = total > 0 ? (failed / total) * 100 : 0
                            const totalPct = Math.min(100, processedPct + failedPct)
                            
                            return (
                              <div className="mt-3">
                                <div className="h-2 bg-gray-200 rounded-full overflow-hidden flex">
                                  {/* Processed (green) */}
                                  <div 
                                    className="h-full bg-green-500 transition-all duration-500"
                                    style={{ width: `${processedPct}%` }}
                                  />
                                  {/* Failed (red) */}
                                  {failed > 0 && (
                                    <div 
                                      className="h-full bg-red-500 transition-all duration-500"
                                      style={{ width: `${failedPct}%` }}
                                    />
                                  )}
                                </div>
                                <div className="flex justify-between text-xs text-gray-500 mt-1">
                                  <span>{completed.toLocaleString()} / {total.toLocaleString()}</span>
                                  <span>{totalPct.toFixed(1)}%</span>
                                </div>
                              </div>
                            )
                          })()
                        )}
                      </>
                    ) : (
                      <p className="text-xs text-gray-500">No progress data available</p>
                    )}
                    
                    {/* Custom Status */}
                    {stageJob.customStatus && (
                      <div className="mt-3 pt-3 border-t border-gray-200">
                        <p className="text-xs text-gray-500 mb-1">Custom Status:</p>
                        <pre className="text-xs bg-white p-2 rounded border border-gray-200 overflow-auto max-h-32">
                          {JSON.stringify(stageJob.customStatus, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
