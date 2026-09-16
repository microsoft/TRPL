// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Folder, CheckCircle2, Clock, FileText, Loader2, Eye, ChevronRight, TrendingUp, Archive, AlertCircle } from 'lucide-react'
import { apiService, ApiRepositoryStatistics } from '@/services/api'
import Toast from '@/components/Toast'
import Pagination from '@/components/Pagination'
import LoadingSpinner from '@/components/LoadingSpinner'
import PageHeader from '@/components/PageHeader'
import SearchInput from '@/components/SearchInput'
import StatusBadge from '@/components/StatusBadge'
import CompletionRateBar from '@/components/CompletionRateBar'
import PaginationOverlay from '@/components/PaginationOverlay'
import { PaginationState } from '@/types'

const RepositoriesPage: React.FC = () => {
  const navigate = useNavigate()
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  const [repositoryStats, setRepositoryStats] = useState<ApiRepositoryStatistics[]>([])
  const [dashboardStats, setDashboardStats] = useState<{
    totalRepositories: number
    totalItems: number
    totalCollections: number
    totalPending: number
    totalReviewed: number
    totalPublishing: number
    totalPublished: number
    totalErrors: number
    overallCompletionRate: number
  }>({
    totalRepositories: 0,
    totalItems: 0,
    totalCollections: 0,
    totalPending: 0,
    totalReviewed: 0,
    totalPublishing: 0,
    totalPublished: 0,
    totalErrors: 0,
    overallCompletionRate: 0
  })
  const [isInitialLoading, setIsInitialLoading] = useState<boolean>(true)
  const [isSearching, setIsSearching] = useState<boolean>(false)
  const [searchQuery, setSearchQuery] = useState<string>('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState<string>('')
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null)
  
  // Pagination
  const [pagination, setPagination] = useState<PaginationState>({
    currentPage: 1,
    itemsPerPage: 20,
    totalItems: 0,
    totalPages: 0
  })
  const [isPaginating, setIsPaginating] = useState(false)

  // Debounce search query and reset to page 1
  useEffect(() => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
    }
    
    debounceTimerRef.current = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery)
      // Reset to page 1 when search changes
      setPagination(prev => ({ ...prev, currentPage: 1 }))
    }, 400)

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
    }
  }, [searchQuery])

  // Fetch overall statistics (for dashboard cards)
  useEffect(() => {
    const fetchStatistics = async () => {
      try {
        const stats = await apiService.getRepositoryStatisticsSummary()
        setDashboardStats(stats)
      } catch (error) {
        console.error('Failed to fetch repository statistics summary:', error)
      }
    }

    fetchStatistics()
  }, [])

  // Fetch paginated repositories for table
  useEffect(() => {
    const fetchRepositories = async () => {
      try {
        // Only show full-page spinner on initial load
        if (isInitialLoading) {
          // Already true on first render
        } else {
          setIsSearching(true)
        }
        
        const response = await apiService.getRepositories({
          search: debouncedSearchQuery || undefined,
          page_number: pagination.currentPage,
          page_size: pagination.itemsPerPage
        })
        setRepositoryStats(response.repositories || [])
        
        // Update pagination with total count from server
        // Use nullish coalescing to handle count=0 as valid value
        const totalCount = response.count ?? response.repositories?.length ?? 0
        setPagination(prev => ({
          ...prev,
          totalItems: totalCount,
          totalPages: Math.ceil(totalCount / prev.itemsPerPage)
        }))
      } catch (error) {
        console.error('Failed to fetch repositories:', error)
        setRepositoryStats([])
        setToast({
          type: 'error',
          message: 'Could not connect to API. Please try again later.'
        })
      } finally {
        setIsInitialLoading(false)
        setIsSearching(false)
      }
    }

    fetchRepositories()
  }, [debouncedSearchQuery, pagination.currentPage, pagination.itemsPerPage])

  const handleRepositoryClick = (repository: string) => {
    navigate(`/repositories/${encodeURIComponent(repository)}/collections`)
  }

  const handleToastClose = () => {
    setToast(null)
  }


  // Calculate additional metrics and enrich data
  const enrichedRepos = useMemo(() => {
    return repositoryStats.map((repo) => {

      const reviewed = repo.reviewed || 0
      const pendingRate = repo.totalItems > 0 ? Math.round((repo.pending / repo.totalItems) * 100) : 0
      const publishedRate = repo.totalItems > 0 ? Math.round((repo.published / repo.totalItems) * 100) : 0
      
      // Determine status
      let status: 'excellent' | 'good' | 'warning' | 'critical' = 'good'
      if (repo.completionRate >= 80) {
        status = 'excellent'
      } else if (repo.completionRate >= 60) {
        status = 'good'
      } else if (repo.completionRate >= 40) {
        status = 'warning'
      } else {
        status = 'critical'
      }

      return {
        ...repo,
        reviewed,
        pendingRate,
        publishedRate,
        status
      }
    })
  }, [repositoryStats])

  // Repositories are already filtered and paginated server-side
  const paginatedRepos = useMemo(() => {
    return enrichedRepos
  }, [enrichedRepos])

  // Handle page change
  const handlePageChange = useCallback(
    async (targetPage: number) => {
      if (isPaginating || targetPage === pagination.currentPage) return

      setIsPaginating(true)
      try {
        setPagination(prev => ({ ...prev, currentPage: targetPage }))
        window.scrollTo({ top: 0, behavior: 'smooth' })
      } catch (error) {
        console.error('Error during page navigation:', error)
        setToast({
          type: 'error',
          message: 'Could not navigate to the requested page'
        })
      } finally {
        setIsPaginating(false)
      }
    },
    [isPaginating, pagination.currentPage]
  )

  // Handle items per page change
  const handleItemsPerPageChange = useCallback(
    async (newItemsPerPage: number) => {
      setPagination(prev => ({
        ...prev,
        itemsPerPage: newItemsPerPage,
        currentPage: 1,
        totalPages: Math.ceil(prev.totalItems / newItemsPerPage)
      }))
    },
    []
  )

  // Pipeline stages
  const pipelineStages = [
    {
      label: 'Pending',
      count: dashboardStats.totalPending,
      icon: Clock,
      bgColor: 'bg-amber-500',
      lightBg: 'bg-amber-50',
      textColor: 'text-amber-700',
      borderColor: 'border-amber-200'
    },
    {
      label: 'Reviewed',
      count: dashboardStats.totalReviewed,
      icon: Eye,
      bgColor: 'bg-blue-500',
      lightBg: 'bg-blue-50',
      textColor: 'text-blue-700',
      borderColor: 'border-blue-200'
    },
    {
      label: 'Publishing',
      count: dashboardStats.totalPublishing || 0,
      icon: Loader2,
      bgColor: 'bg-purple-500',
      lightBg: 'bg-purple-50',
      textColor: 'text-purple-700',
      borderColor: 'border-purple-200'
    },
    {
      label: 'Published',
      count: dashboardStats.totalPublished,
      icon: CheckCircle2,
      bgColor: 'bg-green-500',
      lightBg: 'bg-green-50',
      textColor: 'text-green-700',
      borderColor: 'border-green-200'
    },
    {
      label: 'Errors',
      count: dashboardStats.totalErrors || 0,
      icon: AlertCircle,
      bgColor: 'bg-red-500',
      lightBg: 'bg-red-50',
      textColor: 'text-red-700',
      borderColor: 'border-red-200'
    }
  ]

  if (isInitialLoading) {
    return <LoadingSpinner message="Loading repositories..." />
  }

  return (
    <div className="bg-gray-50">
      <PageHeader
        title="Repositories"
        subtitle="Manage your content repositories and collections"
      />

      {/* Statistics Overview - Single Row */}
      <div className="px-6 py-3 bg-white border-b border-gray-200">
        <div className="flex items-center gap-4">
          {/* Summary Stats */}
          <div className="flex items-center gap-5 pr-5 border-r border-gray-200">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-indigo-100 rounded-lg flex items-center justify-center">
                <Archive className="w-4 h-4 text-indigo-600" />
              </div>
              <div>
                <p className="text-xs text-gray-500">Repositories</p>
                <p className="text-base font-bold text-gray-900">{dashboardStats.totalRepositories}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-teal-100 rounded-lg flex items-center justify-center">
                <Folder className="w-4 h-4 text-teal-600" />
              </div>
              <div>
                <p className="text-xs text-gray-500">Collections</p>
                <p className="text-base font-bold text-gray-900">{dashboardStats.totalCollections.toLocaleString()}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-slate-100 rounded-lg flex items-center justify-center">
                <FileText className="w-4 h-4 text-slate-600" />
              </div>
              <div>
                <p className="text-xs text-gray-500">Total Items</p>
                <p className="text-base font-bold text-gray-900">{dashboardStats.totalItems.toLocaleString()}</p>
              </div>
            </div>
          </div>

          {/* Pipeline Stats */}
          <div className="flex items-center gap-3 flex-1">
            {pipelineStages.map((stage, index) => {
              const Icon = stage.icon
              const percentage = dashboardStats.totalItems > 0 
                ? Math.round((stage.count / dashboardStats.totalItems) * 100) 
                : 0
              
              return (
                <React.Fragment key={stage.label}>
                  <div className={`flex-1 flex items-center gap-2 px-3 py-2 ${stage.lightBg} rounded-lg border ${stage.borderColor}`}>
                    <div className={`w-7 h-7 ${stage.bgColor} rounded flex items-center justify-center flex-shrink-0`}>
                      <Icon className={`w-3.5 h-3.5 text-white ${stage.label === 'Publishing' && stage.count > 0 ? 'animate-spin' : ''}`} />
                    </div>
                    <div className="text-left">
                      <p className="text-base font-bold text-gray-900 leading-tight">{stage.count.toLocaleString()}</p>
                      <p className={`text-xs ${stage.textColor}`}>{stage.label} ({percentage}%)</p>
                    </div>
                  </div>
                  {index < pipelineStages.length - 1 && (
                    <ChevronRight className="w-4 h-4 text-gray-300 flex-shrink-0" />
                  )}
                </React.Fragment>
              )
            })}
          </div>

          {/* Completion Rate */}
          <div className="flex items-center gap-2 pl-5 border-l border-gray-200">
            <TrendingUp className="w-5 h-5 text-green-600" />
            <div>
              <p className="text-base font-bold text-gray-900">{dashboardStats.overallCompletionRate}%</p>
              <p className="text-xs text-gray-500">Complete</p>
            </div>
          </div>
        </div>
      </div>

      {/* Repositories Table */}
      <div className="px-6 py-4 border-t border-gray-200">
        <div>
          {/* Search Box */}
          <div className="mb-4 flex items-center gap-2">
            <SearchInput
              value={searchQuery}
              onChange={setSearchQuery}
              placeholder="Search repositories by name..."
              className="max-w-md"
            />
            {isSearching && <Loader2 className="w-5 h-5 text-gray-400 animate-spin" />}
          </div>

          {repositoryStats.length === 0 ? (
            <div className="text-center py-8">
              <Folder className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-600">No repositories found</p>
            </div>
          ) : paginatedRepos.length === 0 ? (
            <div className="text-center py-8">
              <Folder className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-600">No repositories match your search</p>
            </div>
          ) : (
            <div className={`bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden transition-opacity duration-200 ${isSearching ? 'opacity-60' : ''}`}>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Repository
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Status
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Collections
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Total Items
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Pending
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Reviewed
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Publishing
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Published
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Errors
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Completion Rate
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {paginatedRepos.map((repo) => (
                      <tr
                        key={repo.repository}
                        className="hover:bg-gray-50 transition-colors cursor-pointer group"
                        onClick={() => handleRepositoryClick(repo.repository)}
                      >
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-3">
                            <Folder className="w-5 h-5 text-gray-600 flex-shrink-0" />
                            <div className="min-w-0 flex-1">
                              <h3 className="text-sm font-semibold text-gray-900 group-hover:text-museum-accent transition-colors text-left">
                                {repo.repository}
                              </h3>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <StatusBadge status={repo.status} />
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <Folder className="w-4 h-4 text-gray-400" />
                            <span className="text-sm font-medium text-gray-900">
                              {repo.collections.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <FileText className="w-4 h-4 text-gray-400" />
                            <span className="text-sm font-medium text-gray-900">
                              {repo.totalItems.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <Clock className="w-4 h-4 text-yellow-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {repo.pending.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <Eye className="w-4 h-4 text-blue-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {(repo.reviewed || 0).toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <Loader2 className={`w-4 h-4 text-purple-600 ${(repo.publishing || 0) > 0 ? 'animate-spin' : ''}`} />
                            <span className="text-sm font-medium text-gray-900">
                              {(repo.publishing || 0).toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <CheckCircle2 className="w-4 h-4 text-green-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {repo.published.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <AlertCircle className="w-4 h-4 text-red-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {(repo.errors || 0).toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <CompletionRateBar rate={repo.completionRate} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              
              {/* Pagination */}
              {pagination.totalItems > 0 && (
                <div className="relative">
                  <PaginationOverlay isVisible={isPaginating} />
                  <Pagination
                    pagination={pagination}
                    onPageChange={handlePageChange}
                    onItemsPerPageChange={handleItemsPerPageChange}
                    isLoading={isPaginating}
                  />
                </div>
              )}
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

export default RepositoriesPage
