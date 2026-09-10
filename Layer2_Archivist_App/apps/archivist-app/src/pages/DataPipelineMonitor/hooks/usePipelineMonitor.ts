import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { apiService } from '@/services/api'
import { Database, FileImage, Download, ScanText, Brain, Settings2, Sparkles, LucideIcon } from 'lucide-react'
import type { 
  PipelineStage, 
  ActiveJob, 
  PipelineStats, 
  StageConfig, 
  RetryConfig, 
  ExtraStageConfig, 
  FullPipelineConfig,
  ToastState,
  BackendPipelineStage,
  PipelineActions,
} from '../types'

// Icon mapping from backend string to Lucide component
const ICON_MAP: Record<string, LucideIcon> = {
  Database,
  FileImage,
  Download,
  ScanText,
  Brain,
  Settings2,
  Sparkles
}

// Map backend stage to frontend format - fully dynamic based on backend config
const mapBackendStage = (stage: BackendPipelineStage): PipelineStage => ({
  id: stage.id,
  name: stage.name,
  description: stage.description,
  icon: ICON_MAP[stage.icon] || Database,
  statusField: stage.status_field,
  hasConfig: stage.has_config,
  isAutomated: stage.is_automated,
  canRetry: stage.can_retry,
  triggerEndpoint: stage.trigger_endpoint // Store endpoint for dynamic triggering
})

// Map backend job to frontend format
const mapJob = (job: any): ActiveJob => ({
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

export function usePipelineMonitor() {
  // Pipeline config (from backend)
  const [backendStages, setBackendStages] = useState<BackendPipelineStage[]>([])
  const [pipelineActions, setPipelineActions] = useState<PipelineActions>({})
  const [isLoadingStages, setIsLoadingStages] = useState(true)

  // Pipeline statistics
  const [pipelineStats, setPipelineStats] = useState<PipelineStats | null>(null)
  const [isLoadingStats, setIsLoadingStats] = useState(true)
  const [isRebuilding, setIsRebuilding] = useState(false)

  // Active jobs tracking
  const [activeJobs, setActiveJobs] = useState<ActiveJob[]>([])
  const activeJobsRef = useRef<ActiveJob[]>([])
  const fullPipelineTriggerRef = useRef<string | null>(null)
  /** Avoid duplicate rebuild+load when both poll and jobs fetch see the same completion. */
  const lastSyncOnlyStatsRefreshRef = useRef<Map<string, number>>(new Map())

  // UI State
  const [toast, setToast] = useState<ToastState | null>(null)
  const [triggeringStage, setTriggeringStage] = useState<string | null>(null)
  const [managingJob, setManagingJob] = useState<string | null>(null)

  // Retry config
  const [showRetryConfig, setShowRetryConfig] = useState(false)
  const [retryConfig, setRetryConfig] = useState<RetryConfig>({
    step: 'all',
    max_retries: 3,
    batch_size: 50,
    collection_ids: [],
    force_retry: false
  })
  const [retryCollectionInput, setRetryCollectionInput] = useState('')

  // Global pipeline configuration
  const [showConfigPanel, setShowConfigPanel] = useState(false)
  const [globalConfig, setGlobalConfig] = useState<StageConfig>({
    collection_ids: [],
    batch_size: 100,
    parallel_batches: 20
  })
  const [globalCollectionInput, setGlobalCollectionInput] = useState('')
  const [isLoadingConfig, setIsLoadingConfig] = useState(true)
  const [isSavingConfig, setIsSavingConfig] = useState(false)
  const [configLastSaved, setConfigLastSaved] = useState<string | null>(null)

  // Stage-specific config modal (for stages with hasConfig=true)
  const [showExtraConfig, setShowExtraConfig] = useState(false)
  const [configStageId, setConfigStageId] = useState<string | null>(null)
  const [extraStageConfig, setExtraStageConfig] = useState<ExtraStageConfig>({})

  // Full pipeline configuration modal
  const [showFullPipelineConfig, setShowFullPipelineConfig] = useState(false)
  const [fullPipelineLookbackDraft, setFullPipelineLookbackDraft] = useState('168')
  /** Comma-separated collection names (optional); sent as ``collection_names`` on adhoc runs. */
  const [fullPipelineCollectionInput, setFullPipelineCollectionInput] = useState('')
  const [fullPipelineFromDateDraft, setFullPipelineFromDateDraft] = useState('')
  const [fullPipelineToDateDraft, setFullPipelineToDateDraft] = useState('')
  const [fullPipelineConfig, setFullPipelineConfig] = useState<FullPipelineConfig>({
    lookback_hours: 168,
    run_all_stages: true
  })
  // Pipeline stages (memoized from backend data)
  const pipelineStages = useMemo<PipelineStage[]>(() => {
    return backendStages.map(mapBackendStage)
  }, [backendStages])

  // Load full pipeline config (stages, actions, runtime config) from backend
  const loadFullPipelineConfig = useCallback(async () => {
    setIsLoadingConfig(true)
    try {
      const config = await apiService.getPipelineConfig()
      setBackendStages(config.stages)
      setPipelineActions(config.actions)
      // Also load runtime config into global config state
      setGlobalConfig({
        collection_ids: config.runtime_config.collection_ids || [],
        batch_size: config.runtime_config.batch_size || 100,
        parallel_batches: config.runtime_config.parallel_batches || 20
      })
      // Set last saved time from backend
      if (config.runtime_config.updated_at) {
        setConfigLastSaved(config.runtime_config.updated_at)
      }
    } catch (error) {
      console.error('Failed to load pipeline config:', error)
      // Fallback to empty - UI will show no stages
    } finally {
      setIsLoadingStages(false)
      setIsLoadingConfig(false)
    }
  }, [])

  // Load pipeline statistics (always from cache - fast)
  const loadPipelineStats = useCallback(async () => {
    try {
      const stats = await apiService.getPipelineStatistics()
      setPipelineStats(stats)
    } catch (error) {
      console.error('Failed to load pipeline statistics:', error)
      // Set empty stats on error - will be populated from API on retry
      setPipelineStats({
        total_records: 0,
        by_stage: {},
        overall: { pending: 0, completed: 0, error: 0 },
        active_batches: 0
      })
    } finally {
      setIsLoadingStats(false)
    }
  }, [])

  // Save pipeline config to backend
  const savePipelineConfig = useCallback(async () => {
    setIsSavingConfig(true)
    try {
      const result = await apiService.savePipelineConfig({
        collection_ids: globalConfig.collection_ids || [],
        batch_size: globalConfig.batch_size || 100,
        parallel_batches: globalConfig.parallel_batches || 20
      })
      setConfigLastSaved(result.updated_at)
      setToast({ type: 'success', message: 'Configuration saved' })
    } catch (error) {
      console.error('Failed to save pipeline config:', error)
      setToast({ type: 'error', message: 'Failed to save configuration' })
    } finally {
      setIsSavingConfig(false)
    }
  }, [globalConfig])

  // Reset pipeline config state
  const [isResettingConfig, setIsResettingConfig] = useState(false)

  // Reset pipeline config to defaults
  const resetPipelineConfig = useCallback(async () => {
    setIsResettingConfig(true)
    try {
      await apiService.resetPipelineConfig()
      // Reload full config from backend to get fresh defaults
      await loadFullPipelineConfig()
      setToast({ type: 'success', message: 'Configuration reset to defaults' })
    } catch (error) {
      console.error('Failed to reset pipeline config:', error)
      setToast({ type: 'error', message: 'Failed to reset configuration' })
    } finally {
      setIsResettingConfig(false)
    }
  }, [loadFullPipelineConfig])

  // Rebuild statistics
  const rebuildPipelineStats = useCallback(async () => {
    setIsRebuilding(true)
    try {
      const stats = await apiService.rebuildPipelineStatistics()
      setPipelineStats(stats)
      setToast({ type: 'success', message: 'Pipeline statistics rebuilt successfully' })
    } catch (error) {
      console.error('Failed to rebuild pipeline statistics:', error)
      setToast({ type: 'error', message: 'Failed to rebuild pipeline statistics' })
    } finally {
      setIsRebuilding(false)
    }
  }, [])

  useEffect(() => {
    activeJobsRef.current = activeJobs
  }, [activeJobs])

  useEffect(() => {
    fullPipelineTriggerRef.current = pipelineActions.full_pipeline?.trigger_endpoint ?? null
  }, [pipelineActions])

  const SYNC_ONLY_STATS_DEBOUNCE_MS = 8000

  /** Periodic sync with "run all stages" off: rebuild then reload cached stats (handles fast orchestrations). */
  const refreshStatsAfterSyncOnlyFullPipeline = useCallback(
    (job: ActiveJob) => {
      const ep = fullPipelineTriggerRef.current
      if (!ep || job.triggerEndpoint !== ep || job.runAllStages !== false) return

      const now = Date.now()
      const last = lastSyncOnlyStatsRefreshRef.current.get(job.instanceId)
      if (last !== undefined && now - last < SYNC_ONLY_STATS_DEBOUNCE_MS) return
      lastSyncOnlyStatsRefreshRef.current.set(job.instanceId, now)

      void (async () => {
        await rebuildPipelineStats()
        await loadPipelineStats()
      })()
    },
    [rebuildPipelineStats, loadPipelineStats]
  )

  // Load active jobs from backend (after refreshStatsAfterSyncOnlyFullPipeline — used for sync-only completion)
  const loadActiveJobs = useCallback(async () => {
    try {
      const response = await apiService.getActiveJobs()
      const prev = activeJobsRef.current
      const mapped = response.jobs.map(mapJob).map((job) => {
        const prior = prev.find(
          (p) => p.triggerEndpoint === job.triggerEndpoint && p.instanceId === job.instanceId
        )
        const localSameEndpoint = prev.find((p) => p.triggerEndpoint === job.triggerEndpoint)
        const activeLocal =
          localSameEndpoint &&
          ['Pending', 'Running', 'Suspended'].includes(localSameEndpoint.runtimeStatus)

        // Cosmos can lag behind a trigger or still hold a prior instance's URLs.
        if (
          activeLocal &&
          localSameEndpoint &&
          localSameEndpoint.instanceId !== job.instanceId
        ) {
          return {
            ...localSameEndpoint,
            runAllStages: localSameEndpoint.runAllStages ?? job.runAllStages,
          }
        }

        let merged = job
        if (prior?.runAllStages !== undefined) {
          merged = { ...merged, runAllStages: prior.runAllStages }
        }
        if (prior?.instanceId === job.instanceId) {
          merged = {
            ...merged,
            statusUrl: merged.statusUrl || prior.statusUrl,
            terminateUrl: merged.terminateUrl || prior.terminateUrl,
            suspendUrl: merged.suspendUrl || prior.suspendUrl,
            resumeUrl: merged.resumeUrl || prior.resumeUrl,
          }
        }
        return merged
      })

      const ep = fullPipelineTriggerRef.current
      const terminal = new Set(['Completed', 'Failed', 'Terminated', 'Canceled'])
      const isTerminal = (s: string) => terminal.has(s)

      if (ep) {
        for (const job of mapped) {
          const old = prev.find(
            (p) => p.triggerEndpoint === job.triggerEndpoint && p.instanceId === job.instanceId
          )
          if (
            old &&
            !isTerminal(old.runtimeStatus) &&
            isTerminal(job.runtimeStatus) &&
            job.triggerEndpoint === ep &&
            job.runAllStages === false
          ) {
            console.log(
              'Periodic sync (sync only) reached terminal status via jobs fetch; refreshing stats'
            )
            refreshStatsAfterSyncOnlyFullPipeline(job)
            break
          }
        }
      }

      setActiveJobs(mapped)
    } catch (error) {
      console.error('Failed to load active jobs:', error)
    }
  }, [refreshStatsAfterSyncOnlyFullPipeline])

  // Poll active job status
  const pollJobStatus = useCallback(async (job: ActiveJob) => {
    // Skip polling if no status URL
    if (!job.statusUrl) {
      console.warn(`Job ${job.triggerEndpoint} has no status URL, skipping poll`)
      return
    }
    
    console.log(`Polling status for job: ${job.triggerEndpoint}`, job.statusUrl)
    
    try {
      const status = await apiService.getOrchestratorStatus(job.statusUrl)
      console.log(`Status response for ${job.triggerEndpoint}:`, status)
      
      // Validate status response
      if (!status || !status.runtimeStatus) {
        console.warn(`Invalid status response for ${job.triggerEndpoint}:`, status)
        return
      }
      
      // Parse input if it's a JSON string
      let inputData = status.input
      if (typeof inputData === 'string') {
        try {
          inputData = JSON.parse(inputData)
        } catch {
          // Keep as string if not valid JSON
        }
      }
      
      // Parse output if it's a JSON string
      let outputData = status.output
      if (typeof outputData === 'string') {
        try {
          outputData = JSON.parse(outputData)
        } catch {
          // Keep as string if not valid JSON
        }
      }
      
      // Determine current_stats: output for completed jobs, input for in-progress
      const isCompleted = ['Completed', 'Failed', 'Terminated', 'Canceled'].includes(status.runtimeStatus)
      const currentStats = isCompleted ? (outputData || status.customStatus) : inputData
      
      // Update job status in state - use triggerEndpoint as key
      setActiveJobs(prev => prev.map(j => 
        j.triggerEndpoint === job.triggerEndpoint 
          ? { 
              ...j, 
              runtimeStatus: status.runtimeStatus, 
              customStatus: status.customStatus,
              currentStats: currentStats,
              lastUpdatedTime: status.lastUpdatedTime
            }
          : j
      ))

      // Update backend with latest status - backend will save current_stats based on status
      await apiService.updateActiveJob(job.triggerEndpoint, {
        runtime_status: status.runtimeStatus,
        custom_status: status.customStatus,
        input: inputData,
        output: outputData,
        last_updated_time: status.lastUpdatedTime
      }).catch(err => console.error('Failed to update job in backend:', err))
      
      // Rebuild stats when job completes (actual database changes occurred)
      if (status.runtimeStatus !== job.runtimeStatus) {
        console.log(`Status changed: ${job.runtimeStatus} -> ${status.runtimeStatus}`)
        const isNowComplete = ['Completed', 'Failed', 'Terminated', 'Canceled'].includes(status.runtimeStatus)
        if (isNowComplete) {
          console.log(`Job ${job.triggerEndpoint} completed, rebuilding stats...`)
          const syncOnlyFullPipeline =
            fullPipelineTriggerRef.current === job.triggerEndpoint && job.runAllStages === false
          if (syncOnlyFullPipeline) {
            refreshStatsAfterSyncOnlyFullPipeline(job)
          } else {
            rebuildPipelineStats()
          }
        } else {
          // Just fetch cached stats for intermediate status changes
          loadPipelineStats()
        }
      }

      // Log status based on completion - output for completed, input for in-progress
      if (isCompleted) {
        console.log(`Job ${job.triggerEndpoint} finished with status: ${status.runtimeStatus}`)
        console.log(`Final output for ${job.triggerEndpoint}:`, outputData || status.customStatus || 'No output')
      } else {
        console.log(`Job ${job.triggerEndpoint} in progress - current status:`, inputData || 'No input data')
      }
    } catch (error) {
      console.error('Failed to poll job status:', error)
    }
  }, [loadPipelineStats, rebuildPipelineStats, refreshStatsAfterSyncOnlyFullPipeline])

  // Initial load - only runs once on mount
  useEffect(() => {
    loadFullPipelineConfig() // Load stages, actions, and runtime config
  }, []) // Empty deps - only run on mount

  // Check if any jobs are actively running (for UI indicator)
  const hasActiveJobs = useMemo(() => {
    return activeJobs.some(job => ['Pending', 'Running'].includes(job.runtimeStatus))
  }, [activeJobs])

  // Load stats and active jobs, poll periodically
  useEffect(() => {
    loadPipelineStats()
    loadActiveJobs()

    // Poll cached stats every 30 seconds (fast, non-blocking)
    const statsInterval = setInterval(() => {
      loadPipelineStats()
    }, 30000)

    // Poll active jobs every 10 seconds
    const jobsInterval = setInterval(() => {
      loadActiveJobs()
    }, 10000)

    // When jobs are running, also rebuild stats every 2 minutes for live updates
    let liveStatsInterval: NodeJS.Timeout | null = null
    if (hasActiveJobs) {
      liveStatsInterval = setInterval(() => {
        console.log('Auto-rebuilding stats for active jobs...')
        rebuildPipelineStats()
      }, 2 * 60 * 1000) // 2 minutes
    }

    return () => {
      clearInterval(statsInterval)
      clearInterval(jobsInterval)
      if (liveStatsInterval) clearInterval(liveStatsInterval)
    }
  }, [loadPipelineStats, loadActiveJobs, hasActiveJobs, rebuildPipelineStats])

  // Track jobs that have been initially polled to avoid continuous polling
  const polledJobsRef = useRef<Set<string>>(new Set())

  // Poll active jobs - only poll Pending/Running jobs every 10 seconds
  useEffect(() => {
    // Filter to only pending/running jobs
    const activeRunningJobs = activeJobs.filter(job => 
      ['Pending', 'Running'].includes(job.runtimeStatus)
    )
    
    if (activeRunningJobs.length === 0) {
      polledJobsRef.current.clear()
      return
    }

    // Poll new jobs immediately (only once per job)
    activeRunningJobs.forEach(job => {
      if (!polledJobsRef.current.has(job.triggerEndpoint)) {
        polledJobsRef.current.add(job.triggerEndpoint)
        pollJobStatus(job)
      }
    })

    // Clean up tracked jobs that are no longer active
    const activeEndpoints = new Set(activeRunningJobs.map(j => j.triggerEndpoint))
    polledJobsRef.current.forEach(endpoint => {
      if (!activeEndpoints.has(endpoint)) {
        polledJobsRef.current.delete(endpoint)
      }
    })

    // Then poll every 10 seconds
    const pollInterval = setInterval(() => {
      // Re-filter in case state changed
      const currentActiveJobs = activeJobs.filter(job => 
        ['Pending', 'Running'].includes(job.runtimeStatus)
      )
      currentActiveJobs.forEach(job => pollJobStatus(job))
    }, 10000)

    return () => clearInterval(pollInterval)
  }, [activeJobs, pollJobStatus])

  // Trigger a pipeline stage - fully dynamic using backend config
  const triggerStage = useCallback(async (stage: PipelineStage, config?: StageConfig) => {
    // Check if stage can be triggered (has endpoint and not automated)
    if (!stage.triggerEndpoint || stage.isAutomated) {
      setToast({ type: 'info', message: `${stage.name} is automated and cannot be triggered manually` })
      return
    }

    // Check if job already exists for this trigger endpoint
    const existingJob = activeJobs.find(job => 
      job.triggerEndpoint === stage.triggerEndpoint && 
      ['Pending', 'Running', 'Suspended'].includes(job.runtimeStatus)
    )
    if (existingJob) {
      setToast({ type: 'warning', message: `${stage.name} is already running. Wait for it to complete or terminate it first.` })
      return
    }
    
    setTriggeringStage(stage.id)
    try {
      // Use generic trigger method with endpoint from backend config
      const result = await apiService.triggerPipelineStage(stage.triggerEndpoint, config)
      
      // Validate the response has required fields
      if (!result?.instance_id || !result?.status_url) {
        console.error('Invalid trigger response:', result)
        throw new Error('Pipeline trigger did not return expected instance_id and status_url')
      }
      
      const startedAt = new Date().toISOString()
      
      const newJob: ActiveJob = {
        triggerEndpoint: stage.triggerEndpoint!,
        instanceId: result.instance_id,
        name: stage.name,
        statusUrl: result.status_url,
        terminateUrl: result.terminate_url,
        suspendUrl: result.suspend_url,
        resumeUrl: result.resume_url,
        runtimeStatus: 'Pending',
        startedAt
      }
      
      setActiveJobs(prev => [...prev, newJob])
      setToast({ type: 'success', message: `${stage.name} started successfully` })
      
      // Refresh stats
      loadPipelineStats()
    } catch (error) {
      console.error(`Failed to trigger ${stage.name}:`, error)
      setToast({ type: 'error', message: error instanceof Error ? error.message : `Failed to start ${stage.name}` })
    } finally {
      setTriggeringStage(null)
    }
  }, [loadPipelineStats, activeJobs])

  // Handle stage trigger button click - dynamic based on hasConfig
  const handleTriggerClick = useCallback((stage: PipelineStage) => {
    // Check if stage has special config requirements
    if (stage.hasConfig) {
      // Check if job already exists for this trigger endpoint
      const existingJob = activeJobs.find(job => 
        job.triggerEndpoint === stage.triggerEndpoint && 
        ['Pending', 'Running', 'Suspended'].includes(job.runtimeStatus)
      )
      if (existingJob) {
        setToast({ type: 'warning', message: `${stage.name} is already running. Wait for it to complete or terminate it first.` })
        return
      }
      // Store which stage needs config and show the modal
      setConfigStageId(stage.id)
      setExtraStageConfig({}) // Reset extra config
      setShowExtraConfig(true)
    } else {
      triggerStage(stage, globalConfig)
    }
  }, [triggerStage, globalConfig, activeJobs])

  // Execute stage with special config (for stages with hasConfig=true)
  const executeConfiguredStage = useCallback(async () => {
    if (!configStageId) return
    
    const stage = pipelineStages.find(s => s.id === configStageId)
    if (!stage) return
    
    setShowExtraConfig(false)
    setConfigStageId(null)
    
    // Merge global config with extra stage-specific config
    const config: StageConfig = {
      ...globalConfig,
      ...extraStageConfig
    }
    await triggerStage(stage, config)
  }, [globalConfig, extraStageConfig, triggerStage, pipelineStages, configStageId])
  
  // Get the current stage being configured
  const configuredStage = useMemo((): PipelineStage | null => {
    if (!configStageId) return null
    return pipelineStages.find(s => s.id === configStageId) || null
  }, [configStageId, pipelineStages])

  // Global collection-filter management
  const addGlobalCollectionId = useCallback(() => {
    if (globalCollectionInput.trim()) {
      setGlobalConfig(prev => ({
        ...prev,
        collection_ids: [...(prev.collection_ids || []), globalCollectionInput.trim()]
      }))
      setGlobalCollectionInput('')
    }
  }, [globalCollectionInput])

  const removeGlobalCollectionId = useCallback((index: number) => {
    setGlobalConfig(prev => ({
      ...prev,
      collection_ids: ((prev.collection_ids || []) as string[]).filter((_: string, i: number) => i !== index)
    }))
  }, [])

  // Retry collection-filter management
  const addRetryCollectionId = useCallback(() => {
    if (retryCollectionInput.trim()) {
      setRetryConfig(prev => ({
        ...prev,
        collection_ids: [...(prev.collection_ids || []), retryCollectionInput.trim()]
      }))
      setRetryCollectionInput('')
    }
  }, [retryCollectionInput])

  const removeRetryCollectionId = useCallback((index: number) => {
    setRetryConfig(prev => ({
      ...prev,
      collection_ids: ((prev.collection_ids || []) as string[]).filter((_: string, i: number) => i !== index)
    }))
  }, [])

  // Open full pipeline configuration
  const openFullPipelineConfig = useCallback(() => {
    const fullPipelineAction = pipelineActions.full_pipeline
    if (!fullPipelineAction) {
      setToast({ type: 'error', message: 'Full pipeline action not configured' })
      return
    }
    
    // Check if job already exists for full pipeline endpoint
    const existingJob = activeJobs.find(job => 
      job.triggerEndpoint === fullPipelineAction.trigger_endpoint && 
      ['Pending', 'Running', 'Suspended'].includes(job.runtimeStatus)
    )
    if (existingJob) {
      setToast({ type: 'warning', message: `${fullPipelineAction.name} is already running. Wait for it to complete or terminate it first.` })
      return
    }
    
    setFullPipelineLookbackDraft('168')
    setFullPipelineCollectionInput('')
    setFullPipelineFromDateDraft('')
    setFullPipelineToDateDraft('')
    setFullPipelineConfig({
      lookback_hours: 168,
      run_all_stages: true
    })
    setShowFullPipelineConfig(true)
  }, [activeJobs, pipelineActions])

  const closeFullPipelineConfig = useCallback(() => {
    setShowFullPipelineConfig(false)
  }, [])

  // Execute full pipeline - uses endpoint from config
  const executeFullPipeline = useCallback(async () => {
    const fullPipelineAction = pipelineActions.full_pipeline
    if (!fullPipelineAction) {
      setToast({ type: 'error', message: 'Full pipeline action not configured' })
      return
    }

    const trimmed = fullPipelineLookbackDraft.trim()
    const lookbackHours = Number(trimmed)
    if (
      trimmed === '' ||
      !Number.isFinite(lookbackHours) ||
      !Number.isInteger(lookbackHours) ||
      lookbackHours < 168
    ) {
      setToast({
        type: 'error',
        message:
          'Lookback hours must be a whole number greater than or equal to 168. You can edit the field freely; validation runs when you start the pipeline.',
      })
      return
    }

    const fromTrim = fullPipelineFromDateDraft.trim()
    const toTrim = fullPipelineToDateDraft.trim()
    if (!fromTrim || !toTrim) {
      setToast({
        type: 'error',
        message: 'Adhoc periodic sync requires both From date and To date.',
      })
      return
    }
    if (fromTrim > toTrim) {
      setToast({
        type: 'error',
        message: 'From date must be on or before To date.',
      })
      return
    }

    const collectionNames = fullPipelineCollectionInput
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean)

    try {
      const config: Record<string, unknown> = {
        collection_ids: globalConfig.collection_ids || [],
        batch_size: globalConfig.batch_size || 100,
        parallel_batches: globalConfig.parallel_batches ?? 20,
        lookback_hours: lookbackHours,
        run_all_stages: fullPipelineConfig.run_all_stages,
        from_date: fromTrim,
        to_date: toTrim,
        periodic_run_origin: 'adhoc',
      }
      if (collectionNames.length) {
        config.collection_names = collectionNames
      }
      // Use endpoint from backend config
      const result = await apiService.triggerPipelineStage(fullPipelineAction.trigger_endpoint, config)
      
      // Validate the response has required fields
      if (!result?.instance_id || !result?.status_url) {
        console.error('Invalid trigger response:', result)
        throw new Error('Pipeline trigger did not return expected instance_id and status_url')
      }
      
      const startedAt = new Date().toISOString()
      const triggerEndpoint = fullPipelineAction.trigger_endpoint
      
      const newJob: ActiveJob = {
        triggerEndpoint,
        instanceId: result.instance_id,
        name: fullPipelineAction.name,
        statusUrl: result.status_url,
        terminateUrl: result.terminate_url,
        suspendUrl: result.suspend_url,
        resumeUrl: result.resume_url,
        runtimeStatus: 'Pending',
        startedAt,
        runAllStages: fullPipelineConfig.run_all_stages
      }
      
      setActiveJobs(prev => [...prev, newJob])
      setShowFullPipelineConfig(false)
      setToast({ type: 'success', message: `${fullPipelineAction.name} started successfully` })
      
      // Refresh stats
      loadPipelineStats()
    } catch (error) {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to start pipeline' })
    }
  }, [
    fullPipelineConfig,
    fullPipelineLookbackDraft,
    fullPipelineCollectionInput,
    fullPipelineFromDateDraft,
    fullPipelineToDateDraft,
    globalConfig,
    pipelineActions,
    loadPipelineStats,
  ])

  // Retry failed records
  const triggerRetry = useCallback(async () => {
    setShowRetryConfig(false)
    
    const retryAction = pipelineActions.retry_failed
    if (!retryAction) {
      setToast({ type: 'error', message: 'Retry action not configured' })
      return
    }
    
    try {
      // Use endpoint from backend config
      const result = await apiService.triggerPipelineStage(retryAction.trigger_endpoint, retryConfig)
      
      // Validate the response has required fields
      if (!result?.instance_id || !result?.status_url) {
        console.error('Invalid trigger response:', result)
        throw new Error('Pipeline trigger did not return expected instance_id and status_url')
      }
      
      const startedAt = new Date().toISOString()
      const jobName = `${retryAction.name} (${retryConfig.step})`
      const triggerEndpoint = retryAction.trigger_endpoint
      
      const newJob: ActiveJob = {
        triggerEndpoint,
        instanceId: result.instance_id,
        name: jobName,
        statusUrl: result.status_url,
        terminateUrl: result.terminate_url,
        suspendUrl: result.suspend_url,
        resumeUrl: result.resume_url,
        runtimeStatus: 'Pending',
        startedAt
      }
      
      setActiveJobs(prev => [...prev, newJob])
      setToast({ type: 'success', message: `${retryAction.name} started` })
      
      // Refresh stats
      loadPipelineStats()
    } catch (error) {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to start retry' })
    }
  }, [retryConfig, loadPipelineStats, pipelineActions])

  // Job management
  const terminateJob = useCallback(async (job: ActiveJob) => {
    if (!job.terminateUrl) {
      setToast({ type: 'error', message: 'Terminate URL not available for this job' })
      return
    }
    setManagingJob(job.instanceId)
    try {
      await apiService.terminateOrchestration(
        job.terminateUrl,
        'Terminated by user',
      )
      
      setActiveJobs(prev => prev.map(j => 
        j.triggerEndpoint === job.triggerEndpoint 
          ? { ...j, runtimeStatus: 'Terminated' }
          : j
      ))
      
      await apiService.updateActiveJob(job.triggerEndpoint, { runtime_status: 'Terminated' })
        .catch(err => console.error('Failed to update job in backend:', err))
      
      setToast({ type: 'success', message: `${job.name} terminated successfully` })
    } catch (error) {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to terminate job' })
    } finally {
      setManagingJob(null)
    }
  }, [])

  const suspendJob = useCallback(async (job: ActiveJob) => {
    if (!job.suspendUrl) {
      setToast({ type: 'error', message: 'Suspend URL not available for this job' })
      return
    }
    setManagingJob(job.instanceId)
    try {
      await apiService.suspendOrchestration(
        job.suspendUrl,
        'Suspended by user',
      )
      
      setActiveJobs(prev => prev.map(j => 
        j.triggerEndpoint === job.triggerEndpoint 
          ? { ...j, runtimeStatus: 'Suspended' }
          : j
      ))
      
      await apiService.updateActiveJob(job.triggerEndpoint, { runtime_status: 'Suspended' })
        .catch(err => console.error('Failed to update job in backend:', err))
      
      setToast({ type: 'success', message: `${job.name} paused successfully` })
    } catch (error) {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to pause job' })
    } finally {
      setManagingJob(null)
    }
  }, [])

  const resumeJob = useCallback(async (job: ActiveJob) => {
    if (!job.resumeUrl) {
      setToast({ type: 'error', message: 'Resume URL not available for this job' })
      return
    }
    setManagingJob(job.instanceId)
    try {
      await apiService.resumeOrchestration(
        job.resumeUrl,
        'Resumed by user',
      )
      
      setActiveJobs(prev => prev.map(j => 
        j.triggerEndpoint === job.triggerEndpoint 
          ? { ...j, runtimeStatus: 'Running' }
          : j
      ))
      
      await apiService.updateActiveJob(job.triggerEndpoint, { runtime_status: 'Running' })
        .catch(err => console.error('Failed to update job in backend:', err))
      
      setToast({ type: 'success', message: `${job.name} resumed successfully` })
    } catch (error) {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to resume job' })
    } finally {
      setManagingJob(null)
    }
  }, [])

  // Helper to get job for a special action by trigger endpoint
  const getActionJob = useCallback((actionKey: 'full_pipeline' | 'retry_failed') => {
    const action = pipelineActions[actionKey]
    if (!action) return null
    
    // Find job by trigger endpoint
    return activeJobs.find(job => job.triggerEndpoint === action.trigger_endpoint)
  }, [activeJobs, pipelineActions])

  // Get jobs for special actions
  const fullPipelineJob = getActionJob('full_pipeline')
  const retryFailedJob = getActionJob('retry_failed')

  // Reset state tracking
  const [isResetting, setIsResetting] = useState(false)
  
  // Reset all pipeline jobs - deletes all documents with pipeline_step_ prefix
  const resetAllJobs = useCallback(async () => {
    if (activeJobs.length === 0) {
      setToast({ type: 'info', message: 'No pipeline jobs to reset' })
      return
    }
    
    setIsResetting(true)
    
    try {
      const result = await apiService.resetAllJobs()
      
      // Clear local state
      setActiveJobs([])
      
      // Refresh stats
      await loadPipelineStats()
      
      if (result.error_count === 0) {
        setToast({ type: 'success', message: `Reset complete: ${result.deleted_count} job(s) deleted` })
      } else {
        setToast({ type: 'warning', message: `Reset complete: ${result.deleted_count} deleted, ${result.error_count} failed` })
      }
    } catch (error) {
      console.error('Failed to reset pipeline jobs:', error)
      setToast({ type: 'error', message: 'Failed to reset pipeline jobs' })
    } finally {
      setIsResetting(false)
    }
  }, [activeJobs.length, loadPipelineStats])

  return {
    // Pipeline config
    pipelineStages,
    pipelineActions,
    isLoadingStages,

    // Stats
    pipelineStats,
    isLoadingStats,
    isRebuilding,
    rebuildPipelineStats,
    hasActiveJobs, // For UI indicator that stats may be stale

    // Jobs
    activeJobs,
    fullPipelineJob,
    retryFailedJob,
    triggeringStage,
    managingJob,
    terminateJob,
    suspendJob,
    resumeJob,
    handleTriggerClick,
    resetAllJobs,
    isResetting,

    // Toast
    toast,
    setToast,

    // Config panel
    showConfigPanel,
    setShowConfigPanel,
    globalConfig,
    setGlobalConfig,
    globalCollectionInput,
    setGlobalCollectionInput,
    addGlobalCollectionId,
    removeGlobalCollectionId,
    isLoadingConfig,
    isSavingConfig,
    isResettingConfig,
    configLastSaved,
    savePipelineConfig,
    resetPipelineConfig,

    // Retry config
    showRetryConfig,
    setShowRetryConfig,
    retryConfig,
    setRetryConfig,
    retryCollectionInput,
    setRetryCollectionInput,
    addRetryCollectionId,
    removeRetryCollectionId,
    triggerRetry,

    // Stage config modal (for stages with hasConfig=true)
    showExtraConfig,
    setShowExtraConfig,
    extraStageConfig,
    setExtraStageConfig,
    executeConfiguredStage,
    configuredStage,

    // Full Pipeline config
    showFullPipelineConfig,
    setShowFullPipelineConfig,
    fullPipelineConfig,
    setFullPipelineConfig,
    fullPipelineLookbackDraft,
    setFullPipelineLookbackDraft,
    fullPipelineCollectionInput,
    setFullPipelineCollectionInput,
    fullPipelineFromDateDraft,
    setFullPipelineFromDateDraft,
    fullPipelineToDateDraft,
    setFullPipelineToDateDraft,
    openFullPipelineConfig,
    closeFullPipelineConfig,
    executeFullPipeline,
    loadActiveJobs,
  }
}

