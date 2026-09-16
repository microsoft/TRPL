// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import { Grid3X3, List, Table } from 'lucide-react'

interface CollectionFiltersProps {
  filters: {
    repository: string
    collection: string
  }
  viewMode: 'grid' | 'list' | 'table'
  onFiltersChange: (filters: { repository: string; collection: string }) => void
  onViewModeChange: (mode: 'grid' | 'list' | 'table') => void
  repositories?: string[]
  collections?: string[]
}

const CollectionFilters: React.FC<CollectionFiltersProps> = ({
  filters,
  viewMode,
  onFiltersChange,
  onViewModeChange,
  repositories = [],
  collections = []
}) => {
  const handleFilterChange = (key: 'repository' | 'collection', value: string) => {
    onFiltersChange({ ...filters, [key]: value })
  }

  return (
    <section id="collection-filters" className="bg-white border-b border-museum-200 px-6 py-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between space-y-4 lg:space-y-0">
          <div className="flex flex-wrap items-center gap-4">
            {/* Repository */}
            <div>
              <label className="block text-sm font-medium text-museum-700 mb-1" htmlFor="collection-repository-filter">
                Repository
              </label>
              <p className="text-xs text-museum-600 mb-1">Matching is case-insensitive.</p>
              <select
                id="collection-repository-filter"
                className="px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
                value={filters.repository}
                onChange={(e) => handleFilterChange('repository', e.target.value)}
              >
                <option value="">All Repositories</option>
                {repositories.map((repo) => (
                  <option key={repo} value={repo}>
                    {repo}
                  </option>
                ))}
              </select>
            </div>

            {/* Collection */}
            <div>
              <label className="block text-sm font-medium text-museum-700 mb-1" htmlFor="collection-collection-filter">
                Collection
              </label>
              <select
                id="collection-collection-filter"
                className="px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
                value={filters.collection}
                onChange={(e) => handleFilterChange('collection', e.target.value)}
              >
                <option value="">All Collections</option>
                {collections.map((collection) => (
                  <option key={collection} value={collection}>
                    {collection}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex items-center space-x-3">
            <span className="text-sm text-museum-600">View:</span>
            <button
              className={`p-2 rounded-lg transition-colors ${
                viewMode === 'grid'
                  ? 'text-museum-600 bg-museum-100'
                  : 'text-museum-400 hover:text-museum-600 hover:bg-museum-100'
              }`}
              onClick={() => onViewModeChange('grid')}
              aria-label="Grid view"
              title="Grid view"
            >
              <Grid3X3 className="w-4 h-4" />
            </button>
            <button
              className={`p-2 rounded-lg transition-colors ${
                viewMode === 'list'
                  ? 'text-museum-600 bg-museum-100'
                  : 'text-museum-400 hover:text-museum-600 hover:bg-museum-100'
              }`}
              onClick={() => onViewModeChange('list')}
              aria-label="List view"
              title="List view"
            >
              <List className="w-4 h-4" />
            </button>
            <button
              className={`p-2 rounded-lg transition-colors ${
                viewMode === 'table'
                  ? 'text-museum-600 bg-museum-100'
                  : 'text-museum-400 hover:text-museum-600 hover:bg-museum-100'
              }`}
              onClick={() => onViewModeChange('table')}
              aria-label="Table view"
              title="Table view"
            >
              <Table className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}

export default CollectionFilters
