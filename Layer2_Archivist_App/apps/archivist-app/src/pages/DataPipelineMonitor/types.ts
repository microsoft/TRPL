// Types for Data Pipeline Monitor

// Backend stage configuration (from API)
export interface BackendPipelineStage {
  id: string
  name: string
  description: string
  icon: string
  status_field: string
  has_config: boolean
  trigger_endpoint: string | null
  is_automated: boolean
  can_retry?: boolean
}

// Action configuration (from API)
export interface PipelineAction {
  name: string
  description: string
  trigger_endpoint: string
  button_label?: string
  icon?: string
}

// All actions from config
export interface PipelineActions {
  [key: string]: PipelineAction
}

// Generic stage configuration - accepts any key-value pairs
export type StageConfig = Record<string, any>

export interface PipelineStage {
  id: string
  name: string
  description: string
  icon: React.ComponentType<{ className?: string }>
  statusField: string
  triggerEndpoint?: string | null  // Dynamic endpoint from backend config
  isAutomated?: boolean
  hasConfig?: boolean
  canRetry?: boolean  // Whether this stage can be retried
  stats?: {
    pending: number
    completed: number
    error: number
  }
}

export interface ActiveJob {
  triggerEndpoint: string  // Document ID - one active job per endpoint
  instanceId: string
  name: string
  statusUrl: string
  terminateUrl?: string
  suspendUrl?: string
  resumeUrl?: string
  runtimeStatus: string
  startedAt: string
  startedBy?: string  // User who started the job
  customStatus?: any
  currentStats?: any  // Output for completed jobs, input for in-progress jobs
  lastUpdatedTime?: string
  /** Set when starting periodic sync from UI; used to refresh stats when sync-only run completes. */
  runAllStages?: boolean
}

export interface PipelineStats {
  total_records: number
  by_stage: Record<string, { pending: number; completed: number; error: number }>
  overall: { pending: number; completed: number; error: number }
  active_batches: number
  last_updated?: string
}

// Retry configuration - extends generic config with step selector
export interface RetryConfig extends StageConfig {
  step: string  // 'all' or any stage id from backend
  max_retries?: number
  batch_size?: number
  collection_ids?: string[]
  force_retry?: boolean  // When true: retries ALL records (failed + completed), ignores max_retries, resets retry counts
}

// Extra config for stages with has_config=true (e.g., Content Source Sync)
export type ExtraStageConfig = StageConfig

// Full pipeline configuration
export type FullPipelineConfig = StageConfig

export type PeriodicRunHistoryRow = Record<string, unknown>

export interface ToastState {
  type: 'success' | 'error' | 'info' | 'warning'
  message: string
}

export interface ConfirmDialogState {
  isOpen: boolean
  title: string
  message: string
  onConfirm: () => void
  variant?: 'danger' | 'warning' | 'info'
}

// Map backend job to frontend format
export const mapBackendJob = (job: any): ActiveJob => ({
  triggerEndpoint: job.trigger_endpoint,
  instanceId: job.instance_id,
  name: job.name,
  statusUrl: job.status_url,
  terminateUrl: job.terminate_url,
  suspendUrl: job.suspend_url,
  resumeUrl: job.resume_url,
  runtimeStatus: job.runtime_status,
  startedAt: job.started_at,
  customStatus: job.custom_status,
  currentStats: job.current_stats,
  lastUpdatedTime: job.last_updated_time,
  runAllStages:
    typeof job.run_all_stages === 'boolean'
      ? job.run_all_stages
      : typeof job.runAllStages === 'boolean'
        ? job.runAllStages
        : undefined
})

