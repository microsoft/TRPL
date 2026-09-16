// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { 
  Folder, 
  FileText, 
  CheckCircle2, 
  Clock, 
  Archive,
  ArrowRight,
  Loader2,
  Eye,
  TrendingUp,
  AlertCircle
} from 'lucide-react'
import { apiService, ApiRepositoryStatistics } from '@/services/api'
import Toast from '@/components/Toast'
import LoadingSpinner from '@/components/LoadingSpinner'
import PageHeader from '@/components/PageHeader'
import { useAuth } from '@/contexts/AuthContext'

const DashboardPage: React.FC = () => {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { hasAnyRole, isAuthenticated, isLoading: isAuthLoading } = useAuth()
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info' | 'warning'; message: string } | null>(null)
  const [repositoryStats, setRepositoryStats] = useState<ApiRepositoryStatistics[]>([])
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [dashboardStatsFromAPI, setDashboardStatsFromAPI] = useState<{
    totalItems: number
    totalCollections: number
    totalPending: number
    totalPublishing: number
    totalPublished: number
    totalRepositories: number
    totalReviewed: number
    totalErrors: number
    overallCompletionRate: number
  } | null>(null)

  // Check for access denied redirect
  useEffect(() => {
    const accessDenied = searchParams.get('access_denied')
    const fromPath = searchParams.get('from')
    
    if (accessDenied === 'true') {
      setToast({
        type: 'warning',
        message: `You don't have permission to access ${fromPath || 'that page'}. Redirected to dashboard.`
      })
      // Clear the query params
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams])

  // Fetch dashboard data from API on mount
  useEffect(() => {
    const fetchDashboardData = async () => {
      try {
        setIsLoading(true)
        const [dashboardStats, repoStats] = await Promise.all([
          apiService.getDashboardStatistics(),
          apiService.getRepositories({ page_number: 1, page_size: 100 })
        ])
        
        setDashboardStatsFromAPI(dashboardStats)
        setRepositoryStats(repoStats.repositories || [])
      } catch (error) {
        console.error('Failed to fetch dashboard data:', error)
        setRepositoryStats([])
        setToast({
          type: 'error',
          message: 'Could not connect to API. Please try again later.'
        })
      } finally {
        setIsLoading(false)
      }
    }

    fetchDashboardData()
  }, [])

  const handleToastClose = () => {
    setToast(null)
  }

  const dashboardStats = useMemo(() => {
    if (dashboardStatsFromAPI) {
      return {
        ...dashboardStatsFromAPI,
      }
    }

    return {
      totalItems: 0,
      totalCollections: 0,
      totalPending: 0,
      totalPublishing: 0,
      totalPublished: 0,
      totalRepositories: 0,
      overallCompletionRate: 0,
      totalReviewed: 0,
      totalErrors: 0
    }
  }, [dashboardStatsFromAPI])

  const mostPending = useMemo(() => {
    return [...repositoryStats]
      .filter(repo => repo.pending > 0)
      .sort((a, b) => b.pending - a.pending)
      .slice(0, 4)
  }, [repositoryStats])

  const handleRepositoryClick = (repository: string) => {
    navigate(`/repositories/${encodeURIComponent(repository)}/collections`)
  }

  if (isLoading) {
    return <LoadingSpinner message="Loading dashboard..." />
  }

  // Pipeline stages for visualization
  const pipelineStages = [
    {
      label: 'Pending',
      count: dashboardStats.totalPending,
      icon: Clock,
      color: 'amber',
      bgColor: 'bg-amber-500',
      lightBg: 'bg-amber-50',
      textColor: 'text-amber-700',
      borderColor: 'border-amber-200',
      description: 'Awaiting review'
    },
    {
      label: 'Reviewed',
      count: dashboardStats.totalReviewed,
      icon: Eye,
      color: 'blue',
      bgColor: 'bg-blue-500',
      lightBg: 'bg-blue-50',
      textColor: 'text-blue-700',
      borderColor: 'border-blue-200',
      description: 'Ready to publish'
    },
    {
      label: 'Publishing',
      count: dashboardStats.totalPublishing || 0,
      icon: Loader2,
      color: 'purple',
      bgColor: 'bg-purple-500',
      lightBg: 'bg-purple-50',
      textColor: 'text-purple-700',
      borderColor: 'border-purple-200',
      description: 'In progress'
    },
    {
      label: 'Published',
      count: dashboardStats.totalPublished,
      icon: CheckCircle2,
      color: 'green',
      bgColor: 'bg-green-500',
      lightBg: 'bg-green-50',
      textColor: 'text-green-700',
      borderColor: 'border-green-200',
      description: 'Completed'
    },
    {
      label: 'Errors',
      count: dashboardStats.totalErrors || 0,
      icon: AlertCircle,
      color: 'red',
      bgColor: 'bg-red-500',
      lightBg: 'bg-red-50',
      textColor: 'text-red-700',
      borderColor: 'border-red-200',
      description: 'Pipeline errors'
    }
  ]

  return (
    <div className="bg-gray-50">
      <PageHeader
        title="Dashboard"
        subtitle="Overview of your archival system and collections"
      />

      {/* Notice for users without any group membership */}
      {isAuthenticated && !isAuthLoading && !hasAnyRole && (
        <div className="px-6 pt-6">
          <div className="flex items-start gap-3 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
            <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium">No group membership detected</p>
              <p className="text-amber-700 mt-1">
                Your account doesn't have any group assignments. You have read-only access to browse repositories and collections. 
                Contact your administrator to be added to the appropriate group (Admin, DataFoundations, or Archivist).
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="px-6 py-6">
        <div className="space-y-6">
          
          {/* Summary Cards - Compact Row */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div 
              className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm hover:shadow-md transition-all cursor-pointer group"
              onClick={() => navigate('/repositories')}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 bg-indigo-100 rounded-xl flex items-center justify-center group-hover:bg-indigo-200 transition-colors">
                    <Archive className="w-6 h-6 text-indigo-600" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-gray-500">Repositories</p>
                    <p className="text-3xl font-bold text-gray-900">{dashboardStats.totalRepositories}</p>
                  </div>
                </div>
                <ArrowRight className="w-5 h-5 text-gray-400 group-hover:text-indigo-600 group-hover:translate-x-1 transition-all" />
              </div>
            </div>

            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 bg-teal-100 rounded-xl flex items-center justify-center">
                  <Folder className="w-6 h-6 text-teal-600" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-500">Collections</p>
                  <p className="text-3xl font-bold text-gray-900">{dashboardStats.totalCollections.toLocaleString()}</p>
                </div>
              </div>
            </div>

            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 bg-slate-100 rounded-xl flex items-center justify-center">
                  <FileText className="w-6 h-6 text-slate-600" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-500">Total Items</p>
                  <p className="text-3xl font-bold text-gray-900">{dashboardStats.totalItems.toLocaleString()}</p>
                </div>
              </div>
            </div>
          </div>

          {/* Processing Pipeline */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 bg-gray-50/50">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">Pipeline</h2>
                  <p className="text-sm text-gray-500 mt-0.5">Document workflow status breakdown</p>
                </div>
                <div className="flex items-center gap-2 bg-white px-3 py-1.5 rounded-lg border border-gray-200">
                  <TrendingUp className="w-4 h-4 text-green-600" />
                  <span className="text-sm font-semibold text-gray-900">{dashboardStats.overallCompletionRate}%</span>
                  <span className="text-xs text-gray-500">complete</span>
                </div>
              </div>
            </div>
            
            <div className="p-6">
              {/* Pipeline Visualization */}
              <div className="grid grid-cols-5 gap-3">
                {pipelineStages.map((stage, index) => {
                  const Icon = stage.icon
                  const percentage = dashboardStats.totalItems > 0 
                    ? Math.round((stage.count / dashboardStats.totalItems) * 100) 
                    : 0
                  
                  return (
                    <div key={stage.label} className="relative">
                      <div className={`${stage.lightBg} rounded-xl p-4 border ${stage.borderColor} relative overflow-hidden`}>
                        {/* Background percentage indicator */}
                        <div 
                          className={`absolute bottom-0 left-0 right-0 ${stage.bgColor} opacity-10 transition-all duration-500`}
                          style={{ height: `${Math.max(percentage, 5)}%` }}
                        />
                        
                        <div className="relative z-10">
                          <div className="flex items-center justify-between mb-3">
                            <div className={`w-10 h-10 ${stage.bgColor} rounded-lg flex items-center justify-center`}>
                              <Icon className={`w-5 h-5 text-white ${stage.label === 'Publishing' && stage.count > 0 ? 'animate-spin' : ''}`} />
                            </div>
                            <span className={`text-xs font-medium ${stage.textColor} bg-white/80 px-2 py-1 rounded-full`}>
                              {percentage}%
                            </span>
                          </div>
                          <p className="text-2xl font-bold text-gray-900 mb-1">
                            {stage.count.toLocaleString()}
                          </p>
                          <p className={`text-sm font-medium ${stage.textColor}`}>{stage.label}</p>
                          <p className="text-xs text-gray-500 mt-0.5">{stage.description}</p>
                        </div>
                      </div>
                      
                    </div>
                  )
                })}
              </div>

              {/* Progress Bar */}
              <div className="mt-6 pt-4 border-t border-gray-100">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-gray-700">Overall Progress</span>
                  <span className="text-sm text-gray-500">
                    {dashboardStats.totalPublished.toLocaleString()} of {dashboardStats.totalItems.toLocaleString()} published
                  </span>
                </div>
                <div className="h-3 bg-gray-100 rounded-full overflow-hidden flex">
                  <div 
                    className="bg-green-500 transition-all duration-500"
                    style={{ width: `${dashboardStats.totalItems > 0 ? (dashboardStats.totalPublished / dashboardStats.totalItems) * 100 : 0}%` }}
                  />
                  <div 
                    className="bg-purple-500 transition-all duration-500"
                    style={{ width: `${dashboardStats.totalItems > 0 ? ((dashboardStats.totalPublishing || 0) / dashboardStats.totalItems) * 100 : 0}%` }}
                  />
                  <div 
                    className="bg-blue-500 transition-all duration-500"
                    style={{ width: `${dashboardStats.totalItems > 0 ? (dashboardStats.totalReviewed / dashboardStats.totalItems) * 100 : 0}%` }}
                  />
                  <div 
                    className="bg-amber-500 transition-all duration-500"
                    style={{ width: `${dashboardStats.totalItems > 0 ? (dashboardStats.totalPending / dashboardStats.totalItems) * 100 : 0}%` }}
                  />
                  <div 
                    className="bg-red-500 transition-all duration-500"
                    style={{ width: `${dashboardStats.totalItems > 0 ? ((dashboardStats.totalErrors || 0) / dashboardStats.totalItems) * 100 : 0}%` }}
                  />
                </div>
                <div className="flex items-center gap-4 mt-3">
                  <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-green-500" />
                    <span className="text-xs text-gray-600">Published</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-purple-500" />
                    <span className="text-xs text-gray-600">Publishing</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-blue-500" />
                    <span className="text-xs text-gray-600">Reviewed</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                    <span className="text-xs text-gray-600">Pending</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
                    <span className="text-xs text-gray-600">Errors</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Most Pending Items Section */}
          {mostPending.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
              <div className="px-6 py-4 border-b border-gray-100">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-amber-100 rounded-lg flex items-center justify-center">
                      <Clock className="w-5 h-5 text-amber-600" />
                    </div>
                    <div>
                      <h2 className="text-lg font-semibold text-gray-900">Action Required</h2>
                      <p className="text-sm text-gray-500">Repositories with highest pending items</p>
                    </div>
                  </div>
                  <button
                    onClick={() => navigate('/repositories')}
                    className="text-sm font-medium text-museum-accent hover:underline flex items-center gap-1"
                  >
                    View all
                    <ArrowRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
              <div className="p-4">
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                  {mostPending.map((repo) => (
                    <div
                      key={repo.repository}
                      className="p-4 rounded-xl border border-gray-100 hover:border-amber-300 hover:bg-amber-50/30 transition-all cursor-pointer group"
                      onClick={() => handleRepositoryClick(repo.repository)}
                    >
                      <div className="flex items-start justify-between mb-3">
                        <h3 className="text-sm font-semibold text-gray-900 group-hover:text-amber-700 transition-colors line-clamp-2 flex-1 pr-2">
                          {repo.repository}
                        </h3>
                        <ArrowRight className="w-4 h-4 text-gray-400 group-hover:text-amber-600 flex-shrink-0 mt-0.5" />
                      </div>
                      <div className="space-y-2">
                        <div className="flex items-baseline justify-between">
                          <span className="text-xs text-gray-500">Pending</span>
                          <span className="text-xl font-bold text-amber-600">{repo.pending.toLocaleString()}</span>
                        </div>
                        <div className="w-full bg-gray-100 rounded-full h-1.5">
                          <div
                            className="bg-amber-500 h-1.5 rounded-full transition-all"
                            style={{ width: `${Math.min((repo.pending / repo.totalItems) * 100, 100)}%` }}
                          />
                        </div>
                        <div className="flex items-center justify-between text-xs text-gray-500">
                          <span>{repo.totalItems.toLocaleString()} total</span>
                          <span>{Math.round((repo.pending / repo.totalItems) * 100)}% pending</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Empty State if no pending */}
          {mostPending.length === 0 && dashboardStats.totalItems > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8 text-center">
              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <CheckCircle2 className="w-8 h-8 text-green-600" />
              </div>
              <h3 className="text-lg font-semibold text-gray-900 mb-2">All Caught Up!</h3>
              <p className="text-gray-500 mb-4">No repositories have pending items. Great work!</p>
              <button
                onClick={() => navigate('/repositories')}
                className="inline-flex items-center gap-2 px-4 py-2 bg-museum-accent text-white rounded-lg hover:bg-museum-accent/90 transition-colors"
              >
                <Archive className="w-4 h-4" />
                Browse Repositories
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Toast Notification */}
      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={handleToastClose}
          />
        </div>
      )}
    </div>
  )
}

export default DashboardPage
