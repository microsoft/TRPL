import { 
  Clock, 
  Loader2, 
  CheckCircle2, 
  XCircle, 
  Square, 
  Pause,
  RotateCcw,
  Activity,
  RefreshCw,
  Play
} from 'lucide-react'

// Types
export interface JobStatus {
  has_job?: boolean
  job_id?: string
  status?: string
  total_documents?: number
  processed_documents?: number
  success_documents?: number
  failed_documents?: number
  started_at?: string
  started_by?: string
  error_message?: string
  suspend_url?: string
  resume_url?: string
  terminate_url?: string
}

// =============================================================================
// Status Badge Component
// =============================================================================

interface StatusBadgeProps {
  status?: string
  size?: 'sm' | 'md'
}

const statusConfig: Record<string, { bg: string; text: string; icon: React.ReactNode }> = {
  Pending: { bg: 'bg-gray-100', text: 'text-gray-800', icon: <Clock className="w-3 h-3" /> },
  Running: { bg: 'bg-blue-100', text: 'text-blue-800', icon: <Loader2 className="w-3 h-3 animate-spin" /> },
  Completed: { bg: 'bg-green-100', text: 'text-green-800', icon: <CheckCircle2 className="w-3 h-3" /> },
  Failed: { bg: 'bg-red-100', text: 'text-red-800', icon: <XCircle className="w-3 h-3" /> },
  Terminated: { bg: 'bg-gray-100', text: 'text-gray-800', icon: <Square className="w-3 h-3" /> },
  Canceled: { bg: 'bg-gray-100', text: 'text-gray-800', icon: <Square className="w-3 h-3" /> },
  Suspended: { bg: 'bg-purple-100', text: 'text-purple-800', icon: <Pause className="w-3 h-3" /> },
}

export function StatusBadge({ status, size = 'md' }: StatusBadgeProps) {
  if (!status) return null

  const config = statusConfig[status] || { bg: 'bg-gray-100', text: 'text-gray-800', icon: null }
  const sizeClasses = size === 'sm' ? 'px-1.5 py-0.5 text-xs' : 'px-2 py-1 text-xs'

  return (
    <span className={`inline-flex items-center gap-1 rounded-full font-medium ${config.bg} ${config.text} ${sizeClasses}`}>
      {config.icon}
      {status}
    </span>
  )
}

// =============================================================================
// Progress Bar Component
// =============================================================================

interface ProgressBarProps {
  processed?: number
  total?: number
  success?: number
  failed?: number
  showCounts?: boolean
}

export function ProgressBar({ 
  processed, 
  total,
  success,
  failed,
  showCounts = true 
}: ProgressBarProps) {
  if (!total || total === 0) return null
  
  const percentage = Math.min(100, Math.round(((processed || 0) / total) * 100))
  
  return (
    <div className="w-full">
      {showCounts && (
        <div className="flex justify-between text-sm mb-2">
          <span className="text-gray-600">
            {(processed || 0).toLocaleString()} of {total.toLocaleString()} documents
            {success !== undefined && failed !== undefined && (processed || 0) > 0 && (
              <span className="ml-2 text-xs">
                (<span className="text-green-600">{success.toLocaleString()} ✓</span>
                {failed > 0 && <span className="text-red-600 ml-1">{failed.toLocaleString()} ✗</span>})
              </span>
            )}
          </span>
          <span className="font-semibold text-gray-900">{percentage}%</span>
        </div>
      )}
      <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
        <div 
          className="h-full bg-gradient-to-r from-violet-500 to-purple-500 rounded-full transition-all duration-500 ease-out"
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
}

// =============================================================================
// Job Control Buttons Component
// =============================================================================

interface JobControlButtonsProps {
  isRunning: boolean
  isSuspended: boolean
  canSuspend: boolean
  canResume: boolean
  canTerminate: boolean
  onSuspend: () => void
  onResume: () => void
  onTerminate: () => void
  size?: 'sm' | 'md'
}

export function JobControlButtons({
  isRunning,
  isSuspended,
  canSuspend,
  canResume,
  canTerminate,
  onSuspend,
  onResume,
  onTerminate,
  size = 'md'
}: JobControlButtonsProps) {
  const isActive = isRunning || isSuspended
  if (!isActive) return null

  const sizeClasses = size === 'sm' 
    ? 'px-3 py-1.5 text-sm gap-1.5' 
    : 'px-4 py-2.5 gap-2'
  const iconSize = size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'

  return (
    <div className="flex items-center gap-2">
      {canSuspend && (
        <button
          onClick={onSuspend}
          className={`inline-flex items-center ${sizeClasses} bg-purple-50 text-purple-700 border border-purple-200 rounded-lg font-medium hover:bg-purple-100 transition-colors`}
          title="Pause"
        >
          <Pause className={iconSize} />
          Pause
        </button>
      )}
      
      {canResume && (
        <button
          onClick={onResume}
          className={`inline-flex items-center ${sizeClasses} bg-green-50 text-green-700 border border-green-200 rounded-lg font-medium hover:bg-green-100 transition-colors`}
          title="Resume"
        >
          <RotateCcw className={iconSize} />
          Resume
        </button>
      )}
      
      {canTerminate && (
        <button
          onClick={onTerminate}
          className={`inline-flex items-center ${sizeClasses} bg-red-50 text-red-700 border border-red-200 rounded-lg font-medium hover:bg-red-100 transition-colors`}
          title="Stop"
        >
          <Square className={iconSize} />
          Stop
        </button>
      )}
    </div>
  )
}

// =============================================================================
// Empty State Component
// =============================================================================

interface EmptyJobStateProps {
  message?: string
  subMessage?: string
}

export function EmptyJobState({ 
  message = 'No active job',
  subMessage 
}: EmptyJobStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-6 text-center">
      <div className="p-3 bg-gray-100 rounded-full mb-3">
        <Activity className="w-6 h-6 text-gray-400" />
      </div>
      <p className="text-gray-600 font-medium mb-1">{message}</p>
      {subMessage && (
        <p className="text-sm text-gray-500">{subMessage}</p>
      )}
    </div>
  )
}

// =============================================================================
// Stat Card Component
// =============================================================================

interface StatCardProps {
  icon: React.ReactNode
  iconBg: string
  label: string
  value: string | number
}

export function StatCard({ icon, iconBg, label, value }: StatCardProps) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
      <div className="flex items-center gap-3">
        <div className={`p-2 rounded-lg ${iconBg}`}>
          {icon}
        </div>
        <div>
          <p className="text-sm text-gray-500">{label}</p>
          <p className="text-lg font-bold text-gray-900">
            {typeof value === 'number' ? value.toLocaleString() : value}
          </p>
        </div>
      </div>
    </div>
  )
}

// =============================================================================
// Refresh Button Component
// =============================================================================

interface RefreshButtonProps {
  onClick: () => void
  isLoading: boolean
  className?: string
}

export function RefreshButton({ onClick, isLoading, className = '' }: RefreshButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={isLoading}
      className={`p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50 ${className}`}
      title="Refresh status"
    >
      <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
    </button>
  )
}

// =============================================================================
// Trigger Button Component
// =============================================================================

interface TriggerButtonProps {
  onClick: () => void
  disabled?: boolean
  isRunning?: boolean
  isSuspended?: boolean
  label?: string
  icon?: typeof Play
  className?: string
}

export function TriggerButton({
  onClick,
  disabled = false,
  isRunning = false,
  isSuspended = false,
  label = 'Start Job',
  icon: Icon = Play,
  className = ''
}: TriggerButtonProps) {
  const baseClasses = 'flex-1 inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed'
  const activeClasses = 'bg-gray-100 text-gray-400'
  const defaultClasses = className || 'bg-gradient-to-r from-violet-600 to-purple-600 text-white hover:from-violet-700 hover:to-purple-700 shadow-md hover:shadow-lg'

  const isActive = isRunning || isSuspended

  return (
    <button
      onClick={onClick}
      disabled={disabled || isActive}
      className={`${baseClasses} ${isActive ? activeClasses : defaultClasses}`}
    >
      {isRunning ? (
        <>
          <Loader2 className="w-4 h-4 animate-spin" />
          Processing...
        </>
      ) : isSuspended ? (
        <>
          <Pause className="w-4 h-4" />
          Paused
        </>
      ) : (
        <>
          <Icon className="w-4 h-4" />
          {label}
        </>
      )}
    </button>
  )
}
