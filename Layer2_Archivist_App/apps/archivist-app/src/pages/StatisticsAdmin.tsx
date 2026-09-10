'use client'

import React, { useState, useEffect } from 'react'
import { apiService } from '@/services/api'
import Toast from '@/components/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'

interface OperationStatus {
  loading: boolean;
  success: boolean | null;
  message: string;
  timestamp: string | null;
}

interface StatisticsStatus {
  statisticsExist: boolean;
  lastUpdated: string | null;
  message: string;
}

const StatisticsAdminPage: React.FC = () => {
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  const [statsStatus, setStatsStatus] = useState<StatisticsStatus | null>(null)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  
  const [operations, setOperations] = useState<Record<string, OperationStatus>>({
    clearAll: { loading: false, success: null, message: '', timestamp: null },
    rebuildAll: { loading: false, success: null, message: '', timestamp: null },
    rebuildDashboard: { loading: false, success: null, message: '', timestamp: null },
    rebuildRepositories: { loading: false, success: null, message: '', timestamp: null },
    rebuildCollections: { loading: false, success: null, message: '', timestamp: null },
    rebuildOcrReports: { loading: false, success: null, message: '', timestamp: null },
  })

  const fetchStatus = async () => {
    setLoadingStatus(true)
    try {
      const status = await apiService.getStatisticsStatus()
      setStatsStatus(status)
    } catch (error) {
      console.error('Failed to fetch statistics status:', error)
      setStatsStatus(null)
    } finally {
      setLoadingStatus(false)
    }
  }

  useEffect(() => {
    fetchStatus()
  }, [])

  const updateOperation = (key: string, update: Partial<OperationStatus>) => {
    setOperations(prev => ({
      ...prev,
      [key]: { ...prev[key], ...update }
    }))
  }

  const handleClearAllClick = () => {
    setShowClearConfirm(true)
  }

  const handleConfirmClearAll = async () => {
    updateOperation('clearAll', { loading: true, success: null, message: '' })
    try {
      const result = await apiService.clearAllStatistics()
      updateOperation('clearAll', { 
        loading: false, 
        success: true, 
        message: result.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'success', message: result.message })
      fetchStatus()
    } catch (error: any) {
      updateOperation('clearAll', { 
        loading: false, 
        success: false, 
        message: error.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'error', message: error.message })
    } finally {
      setShowClearConfirm(false)
    }
  }

  const handleRebuildAll = async () => {
    updateOperation('rebuildAll', { loading: true, success: null, message: '' })
    try {
      const result = await apiService.rebuildAllStatistics()
      updateOperation('rebuildAll', { 
        loading: false, 
        success: true, 
        message: result.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'success', message: result.message })
      fetchStatus()
    } catch (error: any) {
      updateOperation('rebuildAll', { 
        loading: false, 
        success: false, 
        message: error.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'error', message: error.message })
    }
  }

  const handleRebuildDashboard = async () => {
    updateOperation('rebuildDashboard', { loading: true, success: null, message: '' })
    try {
      const result = await apiService.rebuildDashboardStatistics()
      updateOperation('rebuildDashboard', { 
        loading: false, 
        success: true, 
        message: result.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'success', message: result.message })
      fetchStatus()
    } catch (error: any) {
      updateOperation('rebuildDashboard', { 
        loading: false, 
        success: false, 
        message: error.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'error', message: error.message })
    }
  }

  const handleRebuildRepositories = async () => {
    updateOperation('rebuildRepositories', { loading: true, success: null, message: '' })
    try {
      const result = await apiService.rebuildRepositoryStatistics()
      updateOperation('rebuildRepositories', { 
        loading: false, 
        success: true, 
        message: result.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'success', message: result.message })
      fetchStatus()
    } catch (error: any) {
      updateOperation('rebuildRepositories', { 
        loading: false, 
        success: false, 
        message: error.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'error', message: error.message })
    }
  }

  const handleRebuildCollections = async () => {
    updateOperation('rebuildCollections', { loading: true, success: null, message: '' })
    try {
      const result = await apiService.rebuildCollectionStatistics()
      updateOperation('rebuildCollections', { 
        loading: false, 
        success: true, 
        message: result.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'success', message: result.message })
      fetchStatus()
    } catch (error: any) {
      updateOperation('rebuildCollections', { 
        loading: false, 
        success: false, 
        message: error.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'error', message: error.message })
    }
  }

  const handleRebuildOcrReports = async () => {
    updateOperation('rebuildOcrReports', { loading: true, success: null, message: '' })
    try {
      const result = await apiService.rebuildAllOcrReports()
      updateOperation('rebuildOcrReports', { 
        loading: false, 
        success: true, 
        message: result.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'success', message: result.message })
    } catch (error: any) {
      updateOperation('rebuildOcrReports', { 
        loading: false, 
        success: false, 
        message: error.message,
        timestamp: new Date().toISOString()
      })
      setToast({ type: 'error', message: error.message })
    }
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Never'
    return new Date(dateStr).toLocaleString()
  }

  const operationCards = [
    {
      key: 'clearAll',
      title: 'Clear All Statistics',
      description: 'Delete all pre-computed statistics from the container. Use before a full rebuild to remove stale data.',
      icon: (
        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      ),
      action: handleClearAllClick,
      buttonText: 'Clear Statistics',
      buttonColor: 'bg-red-600 hover:bg-red-700',
      iconColor: 'text-red-500',
      borderColor: 'border-red-200',
    },
    {
      key: 'rebuildAll',
      title: 'Rebuild All Statistics',
      description: 'Rebuild all statistics (dashboard, repositories, collections, and OCR reports) from source data. Clears existing stats first.',
      icon: (
        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      ),
      action: handleRebuildAll,
      buttonText: 'Rebuild All',
      buttonColor: 'bg-indigo-600 hover:bg-indigo-700',
      iconColor: 'text-indigo-500',
      borderColor: 'border-indigo-200',
    },
    {
      key: 'rebuildDashboard',
      title: 'Rebuild Dashboard Stats',
      description: 'Rebuild only the dashboard statistics (total counts, completion rates). Fast operation.',
      icon: (
        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
      ),
      action: handleRebuildDashboard,
      buttonText: 'Rebuild Dashboard',
      buttonColor: 'bg-emerald-600 hover:bg-emerald-700',
      iconColor: 'text-emerald-500',
      borderColor: 'border-emerald-200',
    },
    {
      key: 'rebuildRepositories',
      title: 'Rebuild Repository Stats',
      description: 'Rebuild statistics for all repositories. Updates item counts, status counts, and completion rates.',
      icon: (
        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
        </svg>
      ),
      action: handleRebuildRepositories,
      buttonText: 'Rebuild Repositories',
      buttonColor: 'bg-amber-600 hover:bg-amber-700',
      iconColor: 'text-amber-500',
      borderColor: 'border-amber-200',
    },
    {
      key: 'rebuildCollections',
      title: 'Rebuild Collection Stats',
      description: 'Rebuild statistics for all collections across all repositories. Most comprehensive rebuild.',
      icon: (
        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
      ),
      action: handleRebuildCollections,
      buttonText: 'Rebuild Collections',
      buttonColor: 'bg-cyan-600 hover:bg-cyan-700',
      iconColor: 'text-cyan-500',
      borderColor: 'border-cyan-200',
    },
    {
      key: 'rebuildOcrReports',
      title: 'Rebuild OCR Reports',
      description: 'Rebuild OCR accuracy reports for all collections. Generates per-resource-type accuracy breakdowns.',
      icon: (
        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      ),
      action: handleRebuildOcrReports,
      buttonText: 'Rebuild OCR Reports',
      buttonColor: 'bg-violet-600 hover:bg-violet-700',
      iconColor: 'text-violet-500',
      borderColor: 'border-violet-200',
    },
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-slate-100 to-slate-200">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-slate-900">Statistics Administration</h1>
              <p className="text-slate-600 mt-1">Manage pre-computed statistics for the Archivist application</p>
            </div>
            <button
              onClick={fetchStatus}
              disabled={loadingStatus}
              className="flex items-center gap-2 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg transition-colors disabled:opacity-50"
            >
              <svg className={`w-4 h-4 ${loadingStatus ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Refresh Status
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Status Card */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-8">
          <div className="flex items-center gap-4">
            <div className={`w-12 h-12 rounded-full flex items-center justify-center ${
              loadingStatus 
                ? 'bg-slate-100' 
                : statsStatus?.statisticsExist 
                  ? 'bg-green-100' 
                  : 'bg-yellow-100'
            }`}>
              {loadingStatus ? (
                <svg className="w-6 h-6 text-slate-400 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              ) : statsStatus?.statisticsExist ? (
                <svg className="w-6 h-6 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                <svg className="w-6 h-6 text-yellow-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              )}
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-900">
                {loadingStatus 
                  ? 'Checking Status...' 
                  : statsStatus?.statisticsExist 
                    ? 'Statistics Available' 
                    : 'Statistics Need Rebuilding'}
              </h2>
              <p className="text-slate-600">
                {loadingStatus 
                  ? 'Loading...' 
                  : statsStatus?.statisticsExist 
                    ? `Last updated: ${formatDate(statsStatus.lastUpdated)}`
                    : 'No pre-computed statistics found. Run a rebuild to generate them.'}
              </p>
            </div>
          </div>
        </div>

        {/* Operations Grid */}
        <h2 className="text-lg font-semibold text-slate-900 mb-4">Operations</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {operationCards.map((card) => {
            const op = operations[card.key]
            return (
              <div 
                key={card.key}
                className={`bg-white rounded-xl shadow-sm border-2 ${card.borderColor} p-6 transition-all hover:shadow-md`}
              >
                <div className={`${card.iconColor} mb-4`}>
                  {card.icon}
                </div>
                <h3 className="text-lg font-semibold text-slate-900 mb-2">{card.title}</h3>
                <p className="text-slate-600 text-sm mb-4 min-h-[3rem]">{card.description}</p>
                
                {/* Status indicator */}
                {op.timestamp && (
                  <div className={`mb-4 p-3 rounded-lg text-sm ${
                    op.success 
                      ? 'bg-green-50 text-green-800 border border-green-200' 
                      : 'bg-red-50 text-red-800 border border-red-200'
                  }`}>
                    <div className="font-medium">
                      {op.success ? '✓ Success' : '✗ Failed'}
                    </div>
                    <div className="text-xs mt-1 opacity-75">{op.message}</div>
                    <div className="text-xs mt-1 opacity-50">{formatDate(op.timestamp)}</div>
                  </div>
                )}

                <button
                  onClick={card.action}
                  disabled={op.loading}
                  className={`w-full py-2.5 px-4 rounded-lg text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 ${card.buttonColor}`}
                >
                  {op.loading ? (
                    <>
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                      </svg>
                      Processing...
                    </>
                  ) : (
                    card.buttonText
                  )}
                </button>
              </div>
            )
          })}
        </div>

        {/* Info Section */}
        <div className="mt-8 bg-blue-50 border border-blue-200 rounded-xl p-6">
          <div className="flex gap-4">
            <div className="text-blue-500">
              <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div>
              <h3 className="font-semibold text-blue-900 mb-2">About Pre-computed Statistics</h3>
              <p className="text-blue-800 text-sm">
                Statistics are pre-computed and stored in a dedicated container for fast dashboard loading. 
                When documents are updated, statistics are incrementally updated. However, if you notice 
                discrepancies or after major data changes, you may need to rebuild statistics manually.
              </p>
              <div className="mt-3 text-sm text-blue-700">
                <strong>Recommended workflow:</strong> Clear All → Rebuild All
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Clear Statistics Confirm Dialog */}
      <ConfirmDialog
        isOpen={showClearConfirm}
        onClose={() => setShowClearConfirm(false)}
        onConfirm={handleConfirmClearAll}
        title="Clear All Statistics"
        message="Are you sure you want to clear ALL statistics? This action cannot be undone. You will need to rebuild statistics after clearing."
        confirmText="Clear All"
        cancelText="Cancel"
        variant="danger"
        isLoading={operations.clearAll.loading}
      />

      {/* Toast */}
      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={() => setToast(null)}
          />
        </div>
      )}
    </div>
  )
}

export default StatisticsAdminPage

