// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { FileText, Clock, CheckCircle, Image as ImageIcon, Folder, CheckCircle2, Loader2, Eye, ChevronRight, TrendingUp, AlertCircle } from 'lucide-react'
import { apiService, ApiCollectionDetails } from '@/services/api'
import Toast from '@/components/Toast'
import Pagination from '@/components/Pagination'
import LoadingSpinner from '@/components/LoadingSpinner'
import PageHeader from '@/components/PageHeader'
import SearchInput from '@/components/SearchInput'
import StatusBadge from '@/components/StatusBadge'
import CompletionRateBar from '@/components/CompletionRateBar'
import PaginationOverlay from '@/components/PaginationOverlay'
import { PaginationState } from '@/types'

interface CollectionInfo {
  collection: string
  items: number
  resourceTypes: number
  resourceTypesList: string[]
  completionRate: number
  pending: number
  reviewed: number
  publishing: number
  published: number
  errors: number
  avgOcrConfidence: number | null
  pendingRate: number
  status: 'excellent' | 'good' | 'warning' | 'critical'
}

const RepositoryDetailPage: React.FC = () => {
  const { repository } = useParams<{ repository: string }>()
  const navigate = useNavigate()
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  const [collectionDetails, setCollectionDetails] = useState<ApiCollectionDetails[]>([])
  const [repositoryStats, setRepositoryStats] = useState<{
    totalCollections: number
    totalItems: number
    totalPending: number
    totalReviewed: number
    totalPublishing: number
    totalPublished: number
    totalErrors: number
    overallCompletionRate: number
  }>({
    totalCollections: 0,
    totalItems: 0,
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

  const decodedRepositoryName = repository ? decodeURIComponent(repository) : ''

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
      if (!decodedRepositoryName) return

      try {
        const stats = await apiService.getRepositoryCollectionStatisticsSummary(decodedRepositoryName)
        setRepositoryStats(stats)
      } catch (error) {
        console.error('Failed to fetch repository collection statistics summary:', error)
      }
    }

    fetchStatistics()
  }, [decodedRepositoryName])

  // Fetch paginated collections for table
  useEffect(() => {
    const fetchCollectionDetails = async () => {
      if (!decodedRepositoryName) {
        setIsInitialLoading(false)
        return
      }

      try {
        // Only show full-page spinner on initial load
        if (isInitialLoading) {
          // Already true on first render
        } else {
          setIsSearching(true)
        }
        
        const response = await apiService.getCollectionsByRepository(decodedRepositoryName, {
          title_contains: debouncedSearchQuery || undefined,
          page_number: pagination.currentPage,
          page_size: pagination.itemsPerPage
        })
        
        // Convert to ApiCollectionDetails format
        const details: ApiCollectionDetails[] = (response.collections || []).map((collection) => ({
          collection: collection.collection,
          repository: collection.repository,
          totalItems: collection.totalItems,
          pending: collection.pending,
          published: collection.published,
          reviewed: collection.reviewed || 0,
          publishing: collection.publishing || 0,
          errors: collection.errors || 0,
          completionRate: collection.completionRate,
          avgOcrConfidence:
            collection.avgOcrConfidence !== undefined && collection.avgOcrConfidence !== null
              ? collection.avgOcrConfidence
              : null,
          resourceTypes: [],
          resourceTypesCount: 1 // Default value
        }))
        
        setCollectionDetails(details)
        
        // Update pagination with total count from server
        setPagination(prev => ({
          ...prev,
          totalItems: response.count || 0,
          totalPages: Math.ceil((response.count || 0) / prev.itemsPerPage)
        }))
      } catch (error) {
        console.error('Failed to fetch collection details:', error)
        setCollectionDetails([])
        setRepositoryStats({
          totalCollections: 0,
          totalItems: 0,
          totalPending: 0,
          totalReviewed: 0,
          totalPublishing: 0,
          totalPublished: 0,
          totalErrors: 0,
          overallCompletionRate: 0
        })
        setToast({
          type: 'error',
          message: 'Could not connect to API. Please try again later.'
        })
      } finally {
        setIsInitialLoading(false)
        setIsSearching(false)
      }
    }

    fetchCollectionDetails()
  }, [decodedRepositoryName, debouncedSearchQuery, pagination.currentPage, pagination.itemsPerPage])

  // Calculate collection info from collection details
  const repositoryData = useMemo(() => {
    // Get collections with resource types from API
    const collections: CollectionInfo[] = collectionDetails.map((detail) => {
      // Use the completion rate from the backend (already calculated with proper precision)
      const completionRate = detail.completionRate || 0
      
      const pendingRate = detail.totalItems > 0
        ? Math.round((detail.pending / detail.totalItems) * 100)
        : 0

      // Determine status
      let status: 'excellent' | 'good' | 'warning' | 'critical' = 'good'
      if (completionRate >= 80) {
        status = 'excellent'
      } else if (completionRate >= 60) {
        status = 'good'
      } else if (completionRate >= 40) {
        status = 'warning'
      } else {
        status = 'critical'
      }

      return {
        collection: detail.collection,
        items: detail.totalItems,
        resourceTypes: detail.resourceTypesCount || detail.resourceTypes.length || 1,
        resourceTypesList: detail.resourceTypes || [],
        completionRate,
        pending: detail.pending,
        reviewed: detail.reviewed,
        publishing: detail.publishing || 0,
        published: detail.published,
        errors: detail.errors || 0,
        avgOcrConfidence:
          detail.avgOcrConfidence !== undefined && detail.avgOcrConfidence !== null
            ? detail.avgOcrConfidence
            : null,
        pendingRate,
        status
      }
    })

    return { collections }
  }, [collectionDetails])

  // Collections are already filtered and paginated server-side
  const paginatedCollections = useMemo(() => {
    return repositoryData.collections
  }, [repositoryData.collections])

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

  const handleCollectionClick = (collection: string) => {
    // Navigate to collections items page for this repository and collection
    navigate(`/repositories/${encodeURIComponent(decodedRepositoryName)}/collections/${encodeURIComponent(collection)}`)
  }

  const handleToastClose = () => {
    setToast(null)
  }

  // Pipeline stages
  const pipelineStages = [
    {
      label: 'Pending',
      count: repositoryStats.totalPending,
      icon: Clock,
      bgColor: 'bg-amber-500',
      lightBg: 'bg-amber-50',
      textColor: 'text-amber-700',
      borderColor: 'border-amber-200'
    },
    {
      label: 'Reviewed',
      count: repositoryStats.totalReviewed,
      icon: Eye,
      bgColor: 'bg-blue-500',
      lightBg: 'bg-blue-50',
      textColor: 'text-blue-700',
      borderColor: 'border-blue-200'
    },
    {
      label: 'Publishing',
      count: repositoryStats.totalPublishing || 0,
      icon: Loader2,
      bgColor: 'bg-purple-500',
      lightBg: 'bg-purple-50',
      textColor: 'text-purple-700',
      borderColor: 'border-purple-200'
    },
    {
      label: 'Published',
      count: repositoryStats.totalPublished,
      icon: CheckCircle2,
      bgColor: 'bg-green-500',
      lightBg: 'bg-green-50',
      textColor: 'text-green-700',
      borderColor: 'border-green-200'
    },
    {
      label: 'Errors',
      count: repositoryStats.totalErrors || 0,
      icon: AlertCircle,
      bgColor: 'bg-red-500',
      lightBg: 'bg-red-50',
      textColor: 'text-red-700',
      borderColor: 'border-red-200'
    }
  ]

  if (isInitialLoading) {
    return <LoadingSpinner message="Loading repository details..." />
  }

  return (
    <div className="flex flex-col bg-gray-50">
      <PageHeader
        title={decodedRepositoryName}
        subtitle="Overview and collections for this repository"
      />

      {/* Statistics Overview - Single Row */}
      <div className="px-6 py-3 bg-white border-b border-gray-200">
        <div className="flex items-center gap-4">
          {/* Summary Stats */}
          <div className="flex items-center gap-5 pr-5 border-r border-gray-200">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-indigo-100 rounded-lg flex items-center justify-center">
                <Folder className="w-4 h-4 text-indigo-600" />
              </div>
              <div>
                <p className="text-xs text-gray-500">Collections</p>
                <p className="text-base font-bold text-gray-900">{repositoryStats.totalCollections}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-slate-100 rounded-lg flex items-center justify-center">
                <FileText className="w-4 h-4 text-slate-600" />
              </div>
              <div>
                <p className="text-xs text-gray-500">Total Items</p>
                <p className="text-base font-bold text-gray-900">{repositoryStats.totalItems.toLocaleString()}</p>
              </div>
            </div>
          </div>

          {/* Pipeline Stats */}
          <div className="flex items-center gap-3 flex-1">
            {pipelineStages.map((stage, index) => {
              const Icon = stage.icon
              const percentage = repositoryStats.totalItems > 0 
                ? Math.round((stage.count / repositoryStats.totalItems) * 100) 
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
              <p className="text-base font-bold text-gray-900">{repositoryStats.overallCompletionRate}%</p>
              <p className="text-xs text-gray-500">Complete</p>
            </div>
          </div>
        </div>
      </div>

      <div className="px-6 py-4">
        {/* Collections Section */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold text-gray-900">Collections</h2>
            {/* Search Box */}
            <div className="flex items-center gap-2">
              <SearchInput
                value={searchQuery}
                onChange={setSearchQuery}
                placeholder="Search collections by name..."
                className="w-80"
              />
              {isSearching && <Loader2 className="w-5 h-5 text-gray-400 animate-spin" />}
            </div>
          </div>

          {repositoryData.collections.length === 0 ? (
            <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
              <ImageIcon className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-600">No collections found in this repository</p>
            </div>
          ) : paginatedCollections.length === 0 ? (
            <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
              <ImageIcon className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-600">No collections match your search</p>
            </div>
          ) : (
            <div className={`bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden transition-opacity duration-200 ${isSearching ? 'opacity-60' : ''}`}>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Collection
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Total Items
                      </th>
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Status
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
                      <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">
                        Report
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {paginatedCollections.map((collection) => (
                      <tr
                        key={collection.collection}
                        className="hover:bg-gray-50 transition-colors cursor-pointer group"
                        onClick={() => handleCollectionClick(collection.collection)}
                      >
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-3">
                            <Folder className="w-5 h-5 text-gray-600 shrink-0" />
                            <div className="min-w-0 flex-1">
                              <h3 className="text-sm font-semibold text-gray-900 group-hover:text-museum-accent transition-colors text-left">
                                {collection.collection}
                              </h3>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <FileText className="w-4 h-4 text-gray-400" />
                            <span className="text-sm font-medium text-gray-900">
                              {collection.items.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <StatusBadge status={collection.status} />
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <Clock className="w-4 h-4 text-yellow-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {collection.pending.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <Eye className="w-4 h-4 text-blue-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {collection.reviewed.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <Loader2 className={`w-4 h-4 text-purple-600 ${collection.publishing > 0 ? 'animate-spin' : ''}`} />
                            <span className="text-sm font-medium text-gray-900">
                              {collection.publishing.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <CheckCircle className="w-4 h-4 text-green-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {collection.published.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <div className="flex items-center space-x-2">
                            <AlertCircle className="w-4 h-4 text-red-600" />
                            <span className="text-sm font-medium text-gray-900">
                              {(collection.errors || 0).toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                          <CompletionRateBar rate={collection.completionRate} />
                        </td>
                        <td className="px-6 py-4 text-center group-hover:bg-gray-50">
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              navigate(`/repositories/${encodeURIComponent(decodedRepositoryName)}/collections/${encodeURIComponent(collection.collection)}/ocr-report`)
                            }}
                            title="View OCR accuracy report"
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 hover:border-indigo-300 transition-colors"
                          >
                            <TrendingUp className="w-3.5 h-3.5" />
                            OCR Report
                          </button>
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

export default RepositoryDetailPage
