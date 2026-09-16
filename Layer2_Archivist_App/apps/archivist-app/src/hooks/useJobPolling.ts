// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useEffect, useRef, useCallback } from 'react'
import { apiService } from '@/services/api'

// Types
export interface JobStatus {
  has_job?: boolean
  job_id?: string
  job_type?: 'query' | 'ids'
  status?: string
  total_documents?: number
  processed_documents?: number
  success_documents?: number
  failed_documents?: number
  started_at?: string
  started_by?: string
  published_by?: string
  published_date?: string
  updated_at?: string
  completed_at?: string
  error_message?: string
  instance_id?: string
  status_url?: string
  terminate_url?: string
  suspend_url?: string
  resume_url?: string
}

export interface OrchestrationStatus {
  runtimeStatus: string
  input?: unknown
  output?: unknown
  customStatus?: unknown
  lastUpdatedTime?: string
}

type JobFetcher = () => Promise<JobStatus | null>
type JobUpdater = (data: { status: string; error_message?: string }) => Promise<void>

interface UseJobPollingOptions {
  fetchJob: JobFetcher
  updateJob: JobUpdater
  pollingInterval?: number
  onComplete?: () => void
}

interface UseJobPollingReturn {
  jobStatus: JobStatus | null
  orchestrationStatus: OrchestrationStatus | null
  isLoading: boolean
  refresh: () => Promise<JobStatus | null>
}

/**
 * Custom hook for polling job status and orchestration status.
 * 
 * Consolidates the repetitive polling logic used across different job types.
 */
export function useJobPolling({
  fetchJob,
  updateJob,
  pollingInterval = 10000,
  onComplete,
}: UseJobPollingOptions): UseJobPollingReturn {
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null)
  const [orchestrationStatus, setOrchestrationStatus] = useState<OrchestrationStatus | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const pollingRef = useRef<NodeJS.Timeout | null>(null)

  // Fetch job status
  const refresh = useCallback(async () => {
    setIsLoading(true)
    try {
      const status = await fetchJob()
      setJobStatus(status)
      return status
    } catch (error) {
      console.error('Failed to fetch job status:', error)
      return null
    } finally {
      setIsLoading(false)
    }
  }, [fetchJob])

  // Poll orchestration status
  const pollOrchestration = useCallback(async () => {
    if (!jobStatus?.status_url) return

    try {
      const orchStatus = await apiService.getOrchestratorStatus(jobStatus.status_url)
      if (orchStatus) {
        setOrchestrationStatus(orchStatus)

        // Update job status in CosmosDB
        try {
          await updateJob({
            status: orchStatus.runtimeStatus,
            error_message: orchStatus.runtimeStatus === 'Failed'
              ? (typeof orchStatus.output === 'string' ? orchStatus.output : 'Job failed')
              : undefined,
          })
        } catch (error) {
          console.error('Failed to update job status:', error)
        }

        // Refresh job status to get updated progress
        const updatedStatus = await fetchJob()
        setJobStatus(updatedStatus)

        // Check for completion
        const completedStatuses = ['Completed', 'Failed', 'Terminated', 'Canceled']
        if (completedStatuses.includes(orchStatus.runtimeStatus)) {
          onComplete?.()
        }
      }
    } catch (error) {
      console.error('Failed to poll orchestration status:', error)
    }
  }, [jobStatus?.status_url, fetchJob, updateJob, onComplete])

  // Initial fetch
  useEffect(() => {
    refresh()
  }, [refresh])

  // Set up polling when job is active
  useEffect(() => {
    const activeStatuses = ['Running', 'Pending', 'Suspended']
    const isActive = jobStatus?.status && activeStatuses.includes(jobStatus.status)

    if (isActive && jobStatus?.status_url) {
      pollOrchestration()
      pollingRef.current = setInterval(pollOrchestration, pollingInterval)
    }

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [jobStatus?.status, jobStatus?.status_url, pollOrchestration, pollingInterval])

  return {
    jobStatus,
    orchestrationStatus,
    isLoading,
    refresh,
  }
}

/**
 * Hook for job control actions (terminate, suspend, resume).
 */
export interface UseJobControlOptions {
  jobStatus: JobStatus | null
  onRefresh: () => Promise<void>
  onSuccess?: (message: string) => void
  onError?: (message: string) => void
}

export interface UseJobControlReturn {
  isActive: boolean
  isRunning: boolean
  isSuspended: boolean
  canTerminate: boolean
  canSuspend: boolean
  canResume: boolean
  terminate: (reason?: string) => Promise<void>
  suspend: (reason?: string) => Promise<void>
  resume: (reason?: string) => Promise<void>
}

export function useJobControl({
  jobStatus,
  onRefresh,
  onSuccess,
  onError,
}: UseJobControlOptions): UseJobControlReturn {
  const status = jobStatus?.status
  const isRunning = status === 'Running' || status === 'Pending'
  const isSuspended = status === 'Suspended'
  const isActive = isRunning || isSuspended

  const canTerminate = isActive && !!jobStatus?.terminate_url
  const canSuspend = isRunning && !!jobStatus?.suspend_url
  const canResume = isSuspended && !!jobStatus?.resume_url

  const terminate = useCallback(async (reason = 'Terminated by user') => {
    if (!jobStatus?.terminate_url) return
    try {
      await apiService.terminateOrchestration(jobStatus.terminate_url, reason)
      onSuccess?.('Job terminated')
      await onRefresh()
    } catch (error) {
      onError?.(error instanceof Error ? error.message : 'Failed to terminate')
    }
  }, [jobStatus?.terminate_url, onRefresh, onSuccess, onError])

  const suspend = useCallback(async (reason = 'Paused by user') => {
    if (!jobStatus?.suspend_url) return
    try {
      await apiService.suspendOrchestration(jobStatus.suspend_url, reason)
      onSuccess?.('Job paused')
      await onRefresh()
    } catch (error) {
      onError?.(error instanceof Error ? error.message : 'Failed to pause')
    }
  }, [jobStatus?.suspend_url, onRefresh, onSuccess, onError])

  const resume = useCallback(async (reason = 'Resumed by user') => {
    if (!jobStatus?.resume_url) return
    try {
      await apiService.resumeOrchestration(jobStatus.resume_url, reason)
      onSuccess?.('Job resumed')
      await onRefresh()
    } catch (error) {
      onError?.(error instanceof Error ? error.message : 'Failed to resume')
    }
  }, [jobStatus?.resume_url, onRefresh, onSuccess, onError])

  return {
    isActive,
    isRunning,
    isSuspended,
    canTerminate,
    canSuspend,
    canResume,
    terminate,
    suspend,
    resume,
  }
}
