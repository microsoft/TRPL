// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useState } from 'react'
import { RefreshCw, Settings2, Zap, RotateCcw, Lock } from 'lucide-react'
import Toast from '@/components/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'
import PageHeader from '@/components/PageHeader'
import { useAuth } from '@/contexts/AuthContext'

import { usePipelineMonitor } from './hooks/usePipelineMonitor'
import {
  StatisticsOverview,
  PipelineConfigPanel,
  PipelineStagesList,
  StatusBadge,
  JobControls,
  RetryConfigModal,
  StageConfigModal,
  PeriodicSyncModal
} from './components'
import type { ActiveJob, ConfirmDialogState } from './types'

export default function DataPipelineMonitor() {
  const { permissions, isAdmin, user } = useAuth()
  const canEdit = permissions.pipelineMonitor.canEdit
  
  const {
    // Pipeline config
    pipelineStages,
    pipelineActions,
    isLoadingStages,

    // Stats
    pipelineStats,
    isLoadingStats,
    isRebuilding,
    rebuildPipelineStats,
    hasActiveJobs,

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
    closeFullPipelineConfig,
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
    executeFullPipeline,
    loadActiveJobs,
  } = usePipelineMonitor()

  // Confirm dialog state (kept in component for UI interaction)
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState>({
    isOpen: false,
    title: '',
    message: '',
    onConfirm: () => {}
  })

  // Handle terminate with confirmation
  const handleTerminate = (job: ActiveJob) => {
    setConfirmDialog({
      isOpen: true,
      title: 'Terminate Job',
      message: `Are you sure you want to terminate "${job.name}"? This action cannot be undone.`,
      onConfirm: () => {
        setConfirmDialog(prev => ({ ...prev, isOpen: false }))
        terminateJob(job)
      },
      variant: 'danger'
    })
  }

  return (
    <div className="bg-gray-50 min-h-full">
      <PageHeader
        title="Data Pipeline Monitor"
        subtitle="Trigger and monitor TRPL data pipeline functions"
      />

      {/* Quick Actions Bar */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        {/* Read-only notice */}
        {!canEdit && (
          <div className="mb-3 flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            <Lock className="w-4 h-4" />
            <span>You have read-only access to this page. Contact an administrator for edit permissions.</span>
          </div>
        )}
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowConfigPanel(!showConfigPanel)}
              disabled={!canEdit}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
                showConfigPanel 
                  ? 'bg-gray-800 text-white' 
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
              title={!canEdit ? 'Edit access required' : undefined}
            >
              <Settings2 className="w-4 h-4" />
              {showConfigPanel ? 'Hide Config' : 'Configure'}
            </button>

            {/* Full Pipeline Action */}
            {pipelineActions.full_pipeline && (
              <div className="flex items-center gap-2">
                {fullPipelineJob && ['Pending', 'Running', 'Suspended'].includes(fullPipelineJob.runtimeStatus) ? (
                  /* Show status and controls when job is active */
                  <div className="flex items-center gap-2 px-3 py-1.5 bg-indigo-50 border border-indigo-200 rounded-lg">
                    <Zap className="w-4 h-4 text-indigo-600" />
                    <span className="text-sm font-medium text-indigo-700">
                      {pipelineActions.full_pipeline.name}
                    </span>
                    <StatusBadge status={fullPipelineJob.runtimeStatus} />
                    <JobControls
                      job={fullPipelineJob}
                      managingJob={managingJob}
                      onResume={resumeJob}
                      onSuspend={suspendJob}
                      onTerminate={handleTerminate}
                      compact
                      disabled={!canEdit}
                      currentUserEmail={user?.email}
                      isAdmin={isAdmin}
                    />
                  </div>
                ) : (
                  /* Show button when no active job */
                  <div className="flex items-center gap-2">
                    <button
                      onClick={openFullPipelineConfig}
                      disabled={!canEdit}
                      className="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-indigo-600 to-purple-600 text-white rounded-lg font-medium hover:from-indigo-700 hover:to-purple-700 transition-all shadow-md hover:shadow-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:from-indigo-600 disabled:hover:to-purple-600"
                      title={!canEdit ? 'Edit access required' : undefined}
                    >
                      <Zap className="w-4 h-4" />
                      Periodic sync
                    </button>
                    {/* Show last completed status if exists */}
                    {fullPipelineJob && !['Pending', 'Running', 'Suspended'].includes(fullPipelineJob.runtimeStatus) && (
                      <StatusBadge status={fullPipelineJob.runtimeStatus} />
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Retry Failed Action */}
            {pipelineActions.retry_failed && (
              <div className="flex items-center gap-2">
                {retryFailedJob && ['Pending', 'Running', 'Suspended'].includes(retryFailedJob.runtimeStatus) ? (
                  /* Show status and controls when job is active */
                  <div className="flex items-center gap-2 px-3 py-1.5 bg-amber-50 border border-amber-200 rounded-lg">
                    <RotateCcw className="w-4 h-4 text-amber-600" />
                    <span className="text-sm font-medium text-amber-700">
                      {pipelineActions.retry_failed.name}
                    </span>
                    <StatusBadge status={retryFailedJob.runtimeStatus} />
                    <JobControls
                      job={retryFailedJob}
                      managingJob={managingJob}
                      onResume={resumeJob}
                      onSuspend={suspendJob}
                      onTerminate={handleTerminate}
                      compact
                      disabled={!canEdit}
                      currentUserEmail={user?.email}
                      isAdmin={isAdmin}
                    />
                  </div>
                ) : (
                  /* Show button when no active job */
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setShowRetryConfig(true)}
                      disabled={!canEdit}
                      className="inline-flex items-center gap-2 px-4 py-2 bg-amber-500 text-white rounded-lg font-medium hover:bg-amber-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-amber-500"
                      title={!canEdit ? 'Edit access required' : undefined}
                    >
                      <RotateCcw className="w-4 h-4" />
                      {pipelineActions.retry_failed.button_label || pipelineActions.retry_failed.name}
                    </button>
                    {/* Show last completed status if exists */}
                    {retryFailedJob && !['Pending', 'Running', 'Suspended'].includes(retryFailedJob.runtimeStatus) && (
                      <StatusBadge status={retryFailedJob.runtimeStatus} />
                    )}
                  </div>
                )}
              </div>
            )}

          </div>

          <div className="flex items-center gap-2">
            {/* Hint when jobs are running */}
            {hasActiveJobs && !isRebuilding && (
              <span className="text-xs text-amber-600 hidden sm:inline">
                Jobs running • Stats refresh every 2 min
              </span>
            )}
            <button
              onClick={rebuildPipelineStats}
              disabled={isLoadingStats || isRebuilding}
              className={`inline-flex items-center gap-2 px-3 py-2 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                hasActiveJobs && !isRebuilding
                  ? 'text-amber-600 hover:text-amber-700 hover:bg-amber-50 border border-amber-200'
                  : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
              }`}
              title={hasActiveJobs ? "Click to refresh stats from database now" : "Refresh statistics from source data"}
            >
              <RefreshCw className={`w-4 h-4 ${isRebuilding ? 'animate-spin' : ''}`} />
              {isRebuilding ? 'Refreshing...' : 'Refresh'}
            </button>
          </div>
        </div>
      </div>

      {/* Global Pipeline Configuration Panel */}
      {showConfigPanel && (
        <PipelineConfigPanel
          config={globalConfig}
          onConfigChange={setGlobalConfig}
          collectionInput={globalCollectionInput}
          onCollectionInputChange={setGlobalCollectionInput}
          onAddCollection={addGlobalCollectionId}
          onRemoveCollection={removeGlobalCollectionId}
          isLoading={isLoadingConfig}
          isSaving={isSavingConfig}
          isResetting={isResettingConfig}
          lastSaved={configLastSaved}
          onSave={savePipelineConfig}
          onReset={() => {
            setConfirmDialog({
              isOpen: true,
              title: 'Reset Pipeline Configuration',
              message: 'Are you sure you want to reset the pipeline configuration to defaults? This will clear all custom settings including repository filters, batch size, and parallel batches.',
              onConfirm: () => {
                setConfirmDialog(prev => ({ ...prev, isOpen: false }))
                resetPipelineConfig()
              },
              variant: 'warning'
            })
          }}
        />
      )}

      <div className="px-6 py-6">
        {/* Statistics Overview */}
        <StatisticsOverview stats={pipelineStats} />

        {/* Pipeline Stages */}
        <PipelineStagesList
          stages={pipelineStages}
          stats={pipelineStats}
          activeJobs={activeJobs}
          triggeringStage={triggeringStage}
          managingJob={managingJob}
          isLoading={isLoadingStages}
          isResetting={isResetting}
          canEdit={canEdit}
          currentUserEmail={user?.email}
          isAdmin={isAdmin}
          onTrigger={handleTriggerClick}
          onResumeJob={resumeJob}
          onSuspendJob={suspendJob}
          onTerminateJob={handleTerminate}
          onResetAll={() => {
            setConfirmDialog({
              isOpen: true,
              title: 'Reset Pipeline Jobs',
              message: `Are you sure you want to delete all ${activeJobs.length} pipeline job record(s) from the database? This will clear the job history but will not affect currently running Azure orchestrations.`,
              onConfirm: () => {
                setConfirmDialog(prev => ({ ...prev, isOpen: false }))
                resetAllJobs()
              },
              variant: 'danger'
            })
          }}
        />
      </div>

      {/* Modals */}
      <RetryConfigModal
        isOpen={showRetryConfig}
        config={retryConfig}
        collectionInput={retryCollectionInput}
        stages={pipelineStages}
        onConfigChange={setRetryConfig}
        onCollectionInputChange={setRetryCollectionInput}
        onAddCollection={addRetryCollectionId}
        onRemoveCollection={removeRetryCollectionId}
        onClose={() => setShowRetryConfig(false)}
        onSubmit={triggerRetry}
      />

      <StageConfigModal
        isOpen={showExtraConfig}
        stage={configuredStage}
        globalConfig={globalConfig}
        extraConfig={extraStageConfig}
        onExtraConfigChange={setExtraStageConfig}
        onClose={() => setShowExtraConfig(false)}
        onSubmit={executeConfiguredStage}
      />

      <PeriodicSyncModal
        isOpen={showFullPipelineConfig}
        globalConfig={globalConfig}
        fullPipelineConfig={fullPipelineConfig}
        lookbackHoursDraft={fullPipelineLookbackDraft}
        onLookbackHoursDraftChange={setFullPipelineLookbackDraft}
        collectionInput={fullPipelineCollectionInput}
        onCollectionInputChange={setFullPipelineCollectionInput}
        fromDateDraft={fullPipelineFromDateDraft}
        onFromDateDraftChange={setFullPipelineFromDateDraft}
        toDateDraft={fullPipelineToDateDraft}
        onToDateDraftChange={setFullPipelineToDateDraft}
        onFullPipelineConfigChange={setFullPipelineConfig}
        onClose={closeFullPipelineConfig}
        onSubmit={executeFullPipeline}
        canEdit={canEdit}
        onToast={(type, message) => setToast({ type, message })}
        onActiveJobsRefresh={() => void loadActiveJobs()}
      />

      {/* Toast Notification */}
      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}

      {/* Confirmation Dialog */}
      <ConfirmDialog
        isOpen={confirmDialog.isOpen}
        onClose={() => setConfirmDialog(prev => ({ ...prev, isOpen: false }))}
        onConfirm={confirmDialog.onConfirm}
        title={confirmDialog.title}
        message={confirmDialog.message}
        variant={confirmDialog.variant}
      />
    </div>
  )
}

