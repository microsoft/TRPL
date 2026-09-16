// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

// 'use client'

// import React from 'react'
// import { PaginationState, SortState, UIState } from '@/types'

// interface TableControlsProps {
//   pagination: PaginationState
//   sort: SortState
//   ui: UIState
//   onPaginationChange: (pagination: PaginationState) => void
//   onSortChange: (sort: SortState) => void
// }

// const TableControls: React.FC<TableControlsProps> = ({
//   pagination,
//   sort,
//   ui,
//   onPaginationChange,
//   onSortChange,
  
// }) => {
//   const handleItemsPerPageChange = (itemsPerPage: number) => {
//     onPaginationChange({
//       ...pagination,
//       itemsPerPage,
//       currentPage: 1 // Reset to first page when changing items per page
//     })
//   }

//   const handleSortChange = (field: SortState['field'], direction: SortState['direction']) => {
//     onSortChange({ field, direction })
//   }

  

//   return (
//     <section className="bg-white border-b border-museum-200 px-6 py-4">
//       <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-3 sm:space-y-0">
//         <div className="flex items-center space-x-4">
//           <div className="flex items-center space-x-2">
//             <span className="text-sm text-museum-600">Show:</span>
//             <select
//               className="px-3 py-1.5 border border-museum-300 rounded text-sm focus:ring-2 focus:ring-museum-500 focus:border-transparent"
//               value={pagination.itemsPerPage}
//               onChange={(e) => handleItemsPerPageChange(Number(e.target.value))}
//               aria-label="Items per page"
//             >
//               <option value={25}>25</option>
//               <option value={50}>50</option>
//               <option value={100}>100</option>
//               <option value={200}>200</option>
//             </select>
//             <span className="text-sm text-museum-600">per page</span>
//           </div>
//           <div className="w-px h-4 bg-museum-300" aria-hidden="true"></div>
//           <div className="flex items-center space-x-2">
//             <span className="text-sm text-museum-600">Sort by:</span>
//             <select
//               className="px-3 py-1.5 border border-museum-300 rounded text-sm focus:ring-2 focus:ring-museum-500 focus:border-transparent"
//               value={`${sort.field}-${sort.direction}`}
//               onChange={(e) => {
//                 const [field, direction] = e.target.value.split('-') as [SortState['field'], SortState['direction']]
//                 handleSortChange(field, direction)
//               }}
//               aria-label="Sort by"
//             >
//               <option value="date-desc">Oldest First</option>
//               <option value="date-asc">Newest First</option>
//               <option value="confidence-asc">Lowest Confidence</option>
//               <option value="confidence-desc">Highest Confidence</option>
//               <option value="title-asc">Title A-Z</option>
//             </select>
//           </div>
//         </div>
//         <div className="flex items-center space-x-2">
//           <span className="text-sm text-museum-600">
//             Showing {((pagination.currentPage - 1) * pagination.itemsPerPage) + 1}-{Math.min(pagination.currentPage * pagination.itemsPerPage, pagination.totalItems)} of {pagination.totalItems} items
//           </span>
          
//         </div>
//       </div>
//     </section>
//   )
// }

// export default TableControls

'use client'

import React from 'react'
import { Upload, Filter } from 'lucide-react'
import { PaginationState, SortState } from '@/types'

interface TableControlsProps {
  pagination: PaginationState
  sort: SortState
  onPaginationChange: (pagination: PaginationState) => void
  onSortChange: (sort: SortState) => void
  onIngestClick?: () => void
  selectedCount?: number
  isIngesting?: boolean
  onIngestByQueryClick?: () => void
  isIngestingByQuery?: boolean
}

const TableControls: React.FC<TableControlsProps> = ({
  pagination,
  sort,
  onPaginationChange,
  onSortChange,
  onIngestClick,
  selectedCount = 0,
  isIngesting = false,
  onIngestByQueryClick,
  isIngestingByQuery = false,
}) => {
  const handleItemsPerPageChange = (itemsPerPage: number) => {
    onPaginationChange({
      ...pagination,
      itemsPerPage,
      currentPage: 1
    })
  }

  const handleSortChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const [field, direction] = e.target.value.split('-') as [SortState['field'], SortState['direction']]
    onSortChange({ field, direction })
  }

  return (
    <section className="bg-white border-b border-museum-200 px-6 py-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-3 sm:space-y-0">
        <div className="flex items-center space-x-4">
          {/* Items per page */}
          <div className="flex items-center space-x-2">
            <span className="text-sm text-museum-600">Show:</span>
            <select
              className="px-3 py-1.5 border border-museum-300 rounded text-sm focus:ring-2 focus:ring-museum-500 focus:border-transparent"
              value={pagination.itemsPerPage}
              onChange={(e) => handleItemsPerPageChange(Number(e.target.value))}
              aria-label="Items per page"
            >
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
            <span className="text-sm text-museum-600">per page</span>
          </div>

          {/* Sort by */}
          <div className="flex items-center space-x-2">
            <span className="text-sm text-museum-600">Sort by:</span>
            <select
              className="px-3 py-1.5 border border-museum-300 rounded text-sm focus:ring-2 focus:ring-museum-500 focus:border-transparent"
              value={`${sort.field}-${sort.direction}`}
              onChange={handleSortChange}
              aria-label="Sort by"
            >
              <option value="created_at-desc">Newest First</option>
              <option value="created_at-asc">Oldest First</option>
              <option value="confidence-desc">Highest Confidence</option>
              <option value="confidence-asc">Lowest Confidence</option>
              <option value="title-asc">Title A-Z</option>
              <option value="title-desc">Title Z-A</option>
            </select>
          </div>

          {/* Ingest Buttons */}
          {(onIngestClick || onIngestByQueryClick) && (
            <div className="flex items-center space-x-2 ml-4 pl-4 border-l border-museum-300">
              {onIngestClick && (
                <button
                  onClick={onIngestClick}
                  disabled={selectedCount === 0 || isIngesting}
                  className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    selectedCount === 0 || isIngesting
                      ? 'bg-museum-200 text-museum-400 cursor-not-allowed'
                      : 'bg-museum-600 text-white hover:bg-museum-700 focus:ring-2 focus:ring-museum-500 focus:ring-offset-2'
                  }`}
                  aria-label={`Ingest ${selectedCount} selected document${selectedCount !== 1 ? 's' : ''}`}
                >
                  <Upload className="w-4 h-4" />
                  <span>
                    {isIngesting 
                      ? 'Ingesting...' 
                      : `Ingest ${selectedCount > 0 ? `(${selectedCount})` : ''}`
                    }
                  </span>
                </button>
              )}
              {onIngestByQueryClick && (
                <button
                  onClick={onIngestByQueryClick}
                  disabled={isIngestingByQuery || isIngesting}
                  className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    isIngestingByQuery || isIngesting
                      ? 'bg-museum-200 text-museum-400 cursor-not-allowed'
                      : 'bg-museum-500 text-white hover:bg-museum-600 focus:ring-2 focus:ring-museum-500 focus:ring-offset-2'
                  }`}
                  aria-label="Ingest documents matching current filters"
                >
                  <Filter className="w-4 h-4" />
                  <span>
                    {isIngestingByQuery 
                      ? 'Ingesting...' 
                      : 'Ingest by Filter'
                    }
                  </span>
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

export default TableControls