// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { ChevronLeft, ChevronRight, ChevronLast, ChevronFirst } from 'lucide-react'
import { PaginationState } from '@/types'

interface PaginationProps {
  pagination: PaginationState
  onPageChange: (page: number) => void | Promise<void>
  onItemsPerPageChange?: (itemsPerPage: number) => void
  itemsPerPageOptions?: number[]
  isLoading?: boolean
}

const Pagination: React.FC<PaginationProps> = ({
  pagination,
  onPageChange,
  onItemsPerPageChange,
  itemsPerPageOptions = [20, 50, 100], // default options
  isLoading = false,
}) => {
  const { currentPage, itemsPerPage, totalItems } = pagination
  const totalPages = Math.ceil(totalItems / itemsPerPage)

  const startItem = totalItems === 0 ? 0 : (currentPage - 1) * itemsPerPage + 1
  const endItem = totalItems === 0 ? 0 : Math.min(currentPage * itemsPerPage, totalItems)

  const getPageNumbers = () => {
    const pages: (number | string)[] = []
    const maxVisiblePages = 5

    if (totalPages <= maxVisiblePages) {
      for (let i = 1; i <= totalPages; i++) pages.push(i)
    } else {
      pages.push(1)
      if (currentPage > 3) pages.push('...')
      const start = Math.max(2, currentPage - 1)
      const end = Math.min(totalPages - 1, currentPage + 1)
      for (let i = start; i <= end; i++) {
        if (i !== 1) pages.push(i)
      }
      if (currentPage < totalPages - 2) pages.push('...')
      if (totalPages > 1) pages.push(totalPages)
    }

    return pages
  }

  const handleFirst = () => currentPage > 1 && onPageChange(1)
  const handlePrevious = () => currentPage > 1 && onPageChange(currentPage - 1)
  const handleNext = () => currentPage < totalPages && onPageChange(currentPage + 1)
  const handlePageClick = (page: number) => onPageChange(page)
  const handleLast = () => currentPage < totalPages && onPageChange(totalPages)

  const handleItemsPerPageChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newItemsPerPage = Number(e.target.value)
    onItemsPerPageChange?.(newItemsPerPage)
    onPageChange(1) // reset to first page
  }

  return (
    <section className="bg-white border-t border-museum-200 px-6 py-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-4 sm:space-y-0">
        
        <div className="text-sm text-museum-600">
          Showing <span className="font-medium text-museum-900">{startItem}</span> to{' '}
          <span className="font-medium text-museum-900">{endItem}</span> of{' '}
          <span className="font-medium text-museum-900">{totalItems}</span> results
        </div>

        {/* Page numbers */}
        <div className="flex items-center space-x-2">
          <button
            className="px-3 py-2 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handleFirst}
            disabled={currentPage === 1 || isLoading}
            aria-label="First page"
            title="Go to first page"
          >
            <ChevronFirst className="w-4 h-4" />
          </button>
          
          <button
            className="px-3 py-2 border border-museum-300 text-museum-500 rounded-lg hover:bg-museum-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handlePrevious}
            disabled={currentPage === 1 || isLoading}
            aria-label="Previous page"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          {getPageNumbers().map((page, index) =>
            page === '...' ? (
              <span key={index} className="px-3 py-2 text-museum-500">
                ...
              </span>
            ) : (
              <button
                key={index}
                className={`px-3 py-2 rounded-lg transition-colors ${
                  page === currentPage
                    ? 'bg-museum-800 text-white'
                    : 'border border-museum-300 text-museum-700 hover:bg-museum-50'
                } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                onClick={() => handlePageClick(page as number)}
                disabled={isLoading}
                aria-label={`Go to page ${page}`}
                aria-current={page === currentPage ? 'page' : undefined}
              >
                {page}
              </button>
            )
          )}

          <button
            className="px-3 py-2 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handleNext}
            disabled={currentPage === totalPages || isLoading}
            aria-label="Next page"
          >
            <ChevronRight className="w-4 h-4" />
          </button>

          <button
            className="px-3 py-2 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handleLast}
            disabled={currentPage === totalPages || isLoading}
            aria-label="Last page"
            title="Go to last page"
          >
            <ChevronLast className="w-4 h-4" />
          </button>
        </div>
      </div>
    </section>
  )
}

export default Pagination
