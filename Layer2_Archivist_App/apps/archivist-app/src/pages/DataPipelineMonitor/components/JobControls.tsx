import { Loader2, PlayCircle, Pause, Square } from 'lucide-react'
import type { ActiveJob } from '../types'

interface JobControlsProps {
  job: ActiveJob
  managingJob: string | null
  onResume: (job: ActiveJob) => void
  onSuspend: (job: ActiveJob) => void
  onTerminate: (job: ActiveJob) => void
  compact?: boolean
  disabled?: boolean
  /** Current user's email for ownership check */
  currentUserEmail?: string
  /** Whether current user is admin (can control any job) */
  isAdmin?: boolean
}

export default function JobControls({ 
  job, 
  managingJob, 
  onResume, 
  onSuspend, 
  onTerminate,
  compact = false,
  disabled = false,
  currentUserEmail,
  isAdmin = false
}: JobControlsProps) {
  const isManaging = managingJob === job.instanceId
  const canManage = ['Pending', 'Running', 'Suspended'].includes(job.runtimeStatus)
  
  // Check if current user can control this job (admin can control any, others only their own)
  const canControlThisJob = (): boolean => {
    if (disabled) return false
    if (isAdmin) return true
    // Non-admin can only control their own jobs
    if (!job.startedBy || !currentUserEmail) return false
    return job.startedBy.toLowerCase() === currentUserEmail.toLowerCase()
  }
  
  const isDisabled = !canControlThisJob() || isManaging
  const disabledTitle = disabled 
    ? 'Edit access required' 
    : (!isAdmin && job.startedBy && currentUserEmail && job.startedBy.toLowerCase() !== currentUserEmail.toLowerCase())
      ? 'Only the user who started this job or an admin can control it'
      : undefined
  
  const iconSize = compact ? 'w-3.5 h-3.5' : 'w-4 h-4'
  const buttonPadding = compact ? 'p-1' : 'p-1.5'

  return (
    <div className="flex items-center gap-1">
      {/* Pause/Resume button */}
      {job.runtimeStatus === 'Suspended' ? (
        <button
          onClick={(e) => { e.stopPropagation(); onResume(job) }}
          disabled={isDisabled}
          className={`${buttonPadding} text-green-600 hover:bg-green-50 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
          title={isDisabled ? disabledTitle : 'Resume'}
        >
          {isManaging ? (
            <Loader2 className={`${iconSize} animate-spin`} />
          ) : (
            <PlayCircle className={iconSize} />
          )}
        </button>
      ) : ['Pending', 'Running'].includes(job.runtimeStatus) && (
        <button
          onClick={(e) => { e.stopPropagation(); onSuspend(job) }}
          disabled={isDisabled}
          className={`${buttonPadding} text-amber-600 hover:bg-amber-50 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
          title={isDisabled ? disabledTitle : 'Pause'}
        >
          {isManaging ? (
            <Loader2 className={`${iconSize} animate-spin`} />
          ) : (
            <Pause className={iconSize} />
          )}
        </button>
      )}
      
      {/* Terminate button */}
      {canManage && (
        <button
          onClick={(e) => { e.stopPropagation(); onTerminate(job) }}
          disabled={isDisabled}
          className={`${buttonPadding} text-red-600 hover:bg-red-50 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
          title={isDisabled ? disabledTitle : 'Terminate'}
        >
          <Square className={iconSize} />
        </button>
      )}
    </div>
  )
}

