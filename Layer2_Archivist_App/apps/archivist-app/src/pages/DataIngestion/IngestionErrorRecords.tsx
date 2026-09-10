'use client'

import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Loader2, RefreshCw, FileText, AlertTriangle } from 'lucide-react'
import { apiService } from '@/services/api'
import Toast from '@/components/Toast'
import Pagination from '@/components/Pagination'
import { PaginationState } from '@/types'

interface FailedDocument {
  id: string
  record_id?: string
  title?: string
  label?: string
  repository?: string
  collection_name?: string
  archivist_status: string
  archivist_error_message?: string
  _ts?: number
}

const IngestionErrorRecords: React.FC = () => {
  const [searchParams] = useSearchParams()
  const errorText = searchParams.get('error') || ''
  const navigate = useNavigate()
  
  const [records, setRecords] = useState<FailedDocument[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  
  // Pagination state
  const [pagination, setPagination] = useState<PaginationState>({
    currentPage: 1,
    itemsPerPage: 20,
    totalItems: 0,
    totalPages: 0
  })

  // Fetch records
  const fetchRecords = useCallback(async (page: number = pagination.currentPage) => {
    setIsLoading(true)
    try {
      const response = await apiService.getFailedDocuments(
        page, 
        pagination.itemsPerPage,
        errorText || undefined
      )
      setRecords(response.documents || [])
      setPagination(prev => ({
        ...prev,
        currentPage: response.page_number,
        totalItems: response.total_count,
        totalPages: response.total_pages
      }))
    } catch (error) {
      console.error('Failed to fetch records:', error)
      setToast({ type: 'error', message: 'Failed to load records' })
    } finally {
      setIsLoading(false)
    }
  }, [errorText, pagination.itemsPerPage])

  useEffect(() => {
    fetchRecords(1)
  }, [errorText, pagination.itemsPerPage])

  const handlePageChange = (page: number) => {
    fetchRecords(page)
  }

  const handleItemsPerPageChange = (itemsPerPage: number) => {
    setPagination(prev => ({
      ...prev,
      itemsPerPage,
      currentPage: 1
    }))
  }

  // Truncate error for display
  const truncateError = (text: string, maxLength: number = 150): string => {
    if (text.length <= maxLength) return text
    return text.substring(0, maxLength) + '...'
  }

  return (
    <div className="bg-gray-50 min-h-screen">
      {/* Header */}
      <div className="bg-museum-green px-6 py-4">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/tools/data-ingestion/errors')}
            className="p-2 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
            title="Back to Error List"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold text-white">Failed Documents</h1>
            <p className="text-white/70 text-sm">Documents with the selected error</p>
          </div>
        </div>
      </div>

      {/* Error Message Display */}
      {errorText && (
        <div className="px-6 py-3 bg-red-50 border-b border-red-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-red-800">Error Message:</p>
              <p className="text-sm text-red-700 break-words whitespace-pre-wrap mt-1">
                {errorText}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Stats Bar */}
      <div className="px-6 py-3 bg-white border-b border-gray-200">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg border border-slate-200">
            <div className="w-7 h-7 bg-slate-500 rounded flex items-center justify-center">
              <FileText className="w-3.5 h-3.5 text-white" />
            </div>
            <div className="text-left">
              <p className="text-base font-bold text-gray-900 leading-tight">{pagination.totalItems.toLocaleString()}</p>
              <p className="text-xs text-slate-700">Affected Documents</p>
            </div>
          </div>
          <button
            onClick={() => fetchRecords(pagination.currentPage)}
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
        {isLoading && records.length === 0 ? (
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-12 flex flex-col items-center justify-center">
            <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            <p className="mt-3 text-sm text-gray-500">Loading records...</p>
          </div>
        ) : records.length === 0 ? (
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-12 text-center">
            <FileText className="w-12 h-12 text-gray-300 mx-auto" />
            <h3 className="mt-4 text-lg font-medium text-gray-900">No records found</h3>
            <p className="mt-2 text-sm text-gray-500">
              No failed documents found with this error message.
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
                    <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900 w-64">
                      Document ID
                    </th>
                    <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                      Title
                    </th>
                    <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900 w-48">
                      Collection
                    </th>
                    {!errorText && (
                      <th className="px-6 py-4 text-left text-sm font-semibold text-gray-900">
                        Error
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {records.map((record, idx) => {
                    const rowNumber = (pagination.currentPage - 1) * pagination.itemsPerPage + idx + 1
                    return (
                      <tr 
                        key={record.id} 
                        className="hover:bg-gray-50 transition-colors cursor-pointer"
                        onClick={() => navigate(`/review/${record.id}`)}
                      >
                        <td className="px-6 py-4 text-left text-sm text-gray-500 w-16">
                          {rowNumber}
                        </td>
                        <td className="px-6 py-4 text-left text-sm font-mono text-gray-900 w-64">
                          <span className="truncate block max-w-[14rem]" title={record.record_id || record.id}>
                            {record.record_id || record.id}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-left text-sm text-gray-900">
                          <div className="break-words">
                            {record.title || record.label || 'Untitled'}
                          </div>
                        </td>
                        <td className="px-6 py-4 text-left text-sm text-gray-600 w-48">
                          {record.collection_name || '-'}
                        </td>
                        {!errorText && (
                          <td className="px-6 py-4 text-left text-sm">
                            <p className="text-red-700 break-words whitespace-pre-wrap">
                              {truncateError(record.archivist_error_message || 'Unknown error')}
                            </p>
                          </td>
                        )}
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

export default IngestionErrorRecords
