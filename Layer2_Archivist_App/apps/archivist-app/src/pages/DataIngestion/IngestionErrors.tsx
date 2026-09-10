'use client'

import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, AlertTriangle, Loader2, RefreshCw, ChevronRight } from 'lucide-react'
import { apiService } from '@/services/api'
import Toast from '@/components/Toast'
import Pagination from '@/components/Pagination'
import { PaginationState } from '@/types'

interface ErrorItem {
  error_text: string
  count: number
}

const IngestionErrors: React.FC = () => {
  const navigate = useNavigate()
  
  const [errors, setErrors] = useState<ErrorItem[]>([])
  const [totalUniqueErrors, setTotalUniqueErrors] = useState<number>(0)
  const [totalErrorCount, setTotalErrorCount] = useState<number>(0)
  const [isLoading, setIsLoading] = useState(true)
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  
  // Pagination state
  const [pagination, setPagination] = useState<PaginationState>({
    currentPage: 1,
    itemsPerPage: 20,
    totalItems: 0,
    totalPages: 0
  })

  // Fetch errors with server-side pagination
  const fetchErrors = useCallback(async (page: number = pagination.currentPage) => {
    setIsLoading(true)
    try {
      const response = await apiService.getFailedErrorsSummary(page, pagination.itemsPerPage)
      setErrors(response.errors || [])
      setTotalUniqueErrors(response.total_unique_errors || 0)
      setTotalErrorCount(response.total_error_count || 0)
      setPagination(prev => ({
        ...prev,
        currentPage: response.page_number,
        totalItems: response.total_unique_errors,
        totalPages: response.total_pages
      }))
    } catch (error) {
      console.error('Failed to fetch errors:', error)
      setToast({ type: 'error', message: 'Failed to load error details' })
    } finally {
      setIsLoading(false)
    }
  }, [pagination.itemsPerPage])

  useEffect(() => {
    fetchErrors(1)
  }, [pagination.itemsPerPage])

  const handlePageChange = (page: number) => {
    fetchErrors(page)
  }

  const handleItemsPerPageChange = (itemsPerPage: number) => {
    setPagination(prev => ({
      ...prev,
      itemsPerPage,
      currentPage: 1
    }))
  }

  // Truncate error message for display
  const truncateError = (text: string, maxLength: number = 200): string => {
    if (text.length <= maxLength) return text
    return text.substring(0, maxLength) + '...'
  }

  return (
    <div className="bg-gray-50 min-h-screen">
      {/* Header */}
      <div className="bg-museum-green px-6 py-4">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/tools/data-ingestion')}
            className="p-2 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
            title="Back to Data Ingestion"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold text-white">Failed Documents</h1>
            <p className="text-white/70 text-sm">Documents that failed during ingestion</p>
          </div>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="px-6 py-3 bg-white border-b border-gray-200">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 px-3 py-2 bg-red-50 rounded-lg border border-red-200">
              <div className="w-7 h-7 bg-red-500 rounded flex items-center justify-center">
                <AlertTriangle className="w-3.5 h-3.5 text-white" />
              </div>
              <div className="text-left">
                <p className="text-base font-bold text-gray-900 leading-tight">{totalUniqueErrors.toLocaleString()}</p>
                <p className="text-xs text-red-700">Unique Errors</p>
              </div>
            </div>
            <div className="flex items-center gap-2 px-3 py-2 bg-amber-50 rounded-lg border border-amber-200">
              <div className="w-7 h-7 bg-amber-500 rounded flex items-center justify-center">
                <AlertTriangle className="w-3.5 h-3.5 text-white" />
              </div>
              <div className="text-left">
                <p className="text-base font-bold text-gray-900 leading-tight">{totalErrorCount.toLocaleString()}</p>
                <p className="text-xs text-amber-700">Total Documents</p>
              </div>
            </div>
          </div>
          <button
            onClick={() => fetchErrors(pagination.currentPage)}
            disabled={isLoading}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="px-6 py-4">
        {isLoading && errors.length === 0 ? (
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-12 flex flex-col items-center justify-center">
            <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            <p className="mt-3 text-sm text-gray-500">Loading error details...</p>
          </div>
        ) : errors.length === 0 && !isLoading ? (
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-12 text-center">
            <AlertTriangle className="w-12 h-12 text-gray-300 mx-auto" />
            <h3 className="mt-4 text-lg font-medium text-gray-900">No failed documents</h3>
            <p className="mt-2 text-sm text-gray-500">
              All documents have been processed successfully.
            </p>
          </div>
        ) : (
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
            {/* Table */}
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900 w-16">
                      #
                    </th>
                    <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                      Error Description
                    </th>
                    <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900 w-32">
                      Count
                    </th>
                    <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900 w-40">
                      
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {errors.map((error, idx) => {
                    const rowNumber = (pagination.currentPage - 1) * pagination.itemsPerPage + idx + 1
                    return (
                      <tr 
                        key={idx} 
                        className="hover:bg-gray-50 cursor-pointer transition-colors group"
                        onClick={() => navigate(`/tools/data-ingestion/errors/records?error=${encodeURIComponent(error.error_text)}`)}
                      >
                        <td className="px-6 py-4 text-left text-sm text-gray-500 w-16">
                          {rowNumber}
                        </td>
                        <td className="px-6 py-4 text-left text-sm text-gray-900">
                          <div className="max-w-3xl">
                            <p className="break-words whitespace-pre-wrap" title={error.error_text}>
                              {truncateError(error.error_text)}
                            </p>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left">
                          <span className="inline-flex items-center justify-center px-3 py-1 bg-red-100 text-red-700 font-semibold text-sm rounded-full min-w-[3rem]">
                            {error.count.toLocaleString()}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-left">
                          <span className="inline-flex items-center gap-1 text-sm text-museum-accent group-hover:text-museum-accent-dark font-medium">
                            View Records
                            <ChevronRight className="w-4 h-4" />
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {pagination.totalPages > 1 && (
              <Pagination
                pagination={pagination}
                onPageChange={handlePageChange}
                onItemsPerPageChange={handleItemsPerPageChange}
                itemsPerPageOptions={[10, 20, 50, 100]}
                isLoading={isLoading}
              />
            )}
          </div>
        )}
      </div>

      {/* Toast */}
      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  )
}

export default IngestionErrors
