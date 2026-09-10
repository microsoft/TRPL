'use client'

import { useState, useEffect } from 'react'
import { Search, X } from 'lucide-react'
import { FilterState } from '@/types'

interface SearchFiltersProps {
  filters: FilterState
  onFiltersChange: (filters: FilterState) => void
  onSearch: (updatedFilters: FilterState) => void
  onAdvancedFilters: () => void
  repositories?: string[]
  collections?: string[]
  resourceTypes?: string[]
}

const SearchFilters: React.FC<SearchFiltersProps> = ({
  filters,
  onFiltersChange,
  onSearch,
  repositories = [],
  collections = [],
  resourceTypes = [],
}) => {
  const [searchValue, setSearchValue] = useState(filters.search)

  // Sync with parent
  useEffect(() => {
    setSearchValue(filters.search)
  }, [filters.search])

  const handleSearchChange = (value: string) => {
    setSearchValue(value)
   // onFiltersChange({ ...filters, search: value })
  }

  const handleFilterChange = (key: keyof FilterState, value: string) => {
    onFiltersChange({ ...filters, [key]: value })
  }

  const handleSearchClick = () => {
    const updatedFilters = { ...filters, search: searchValue }
    onFiltersChange(updatedFilters)
    onSearch(updatedFilters)
  }

  return (
    <section className="bg-white border-b border-museum-200 px-4 sm:px-6 py-6 w-full">
      <div className="space-y-6">
        {/* Primary Search */}
        <div className="flex flex-col sm:flex-row sm:flex-wrap items-stretch sm:items-center gap-3 w-full">
          {/* Search Input */}
<div className="relative flex-1 min-w-60 w-full sm:max-w-md">
  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
    <Search className="w-4 h-4 text-museum-500" aria-hidden="true" />
  </div>
  <input
    type="text"
              className="w-full pl-10 pr-10 py-2 border border-museum-300 rounded-md focus:ring-2 focus:ring-museum-500 focus:border-transparent placeholder-museum-700 placeholder:text-sm text-museum-900 text-sm"
    placeholder="Search by title"
    value={searchValue}
    onChange={(e) => handleSearchChange(e.target.value)}
    onKeyDown={(e) => e.key === 'Enter' && handleSearchClick()}
    aria-label="Search archival records"
  />
  <div className="absolute inset-y-0 right-0 pr-2 flex items-center">
    <button
      type="button"
      disabled={!searchValue}
      onClick={() => {
        if (!searchValue) return
        setSearchValue('')
        onFiltersChange({ ...filters, search: '' })
      }}
      className={`p-1 rounded-full transition-colors 
        ${searchValue
          ? 'text-museum-500 hover:text-museum-700 hover:bg-museum-100'
          : 'opacity-40 cursor-not-allowed text-museum-400'
        }`}
      aria-label="Clear search and reset filters"
    >
      <X className="w-4 h-4" />
    </button>
  </div>
</div>

<div className="flex sm:w-auto w-full justify-end sm:justify-start">
  <button
    disabled={!searchValue}
    className={`w-full sm:w-auto px-4 py-2 rounded-md flex items-center justify-center space-x-1.5 text-sm transition-colors
      ${searchValue
        ? 'bg-museum-accent text-white hover:bg-museum-700'
        : 'bg-museum-accent text-white cursor-not-allowed'
      }`}
    onClick={handleSearchClick}
  >
    <Search className="w-3.5 h-3.5" />
    <span>Search</span>
  </button>
</div>
        </div>


        {/* Detailed Filters Row */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
          {/* Repository */}
          <div>
            <label
              htmlFor="repository-filter"
              className="block text-sm text-museum-700 mb-1 font-bold text-left"
            >
              Repository
            </label>
            <p className="text-xs text-museum-600 mb-1 text-left">Matching is case-insensitive.</p>
            <select
              id="repository-filter"
              className="w-full px-3 py-1.5 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
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
            <label
              htmlFor="collection-filter"
              className="block text-sm text-museum-700 mb-1 font-bold text-left"
            >
              Collection
            </label>
            <select
              id="collection-filter"
              className="w-full px-3 py-1.5 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
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

          {/* Resource Type */}
          <div>
            <label
              htmlFor="resource-type-filter"
              className="block text-sm text-museum-700 mb-1 font-bold text-left"
            >
              Resource Type
            </label>
            <select
              id="resource-type-filter"
              className="w-full px-3 py-1.5 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
              value={filters.resourceType}
              onChange={(e) => handleFilterChange('resourceType', e.target.value)}
            >
              <option value="">All Types</option>
              {resourceTypes.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </div>

          {/* Confidence */}
          <div>
            <label
              htmlFor="confidence-filter"
              className="block text-sm font-bold text-museum-700 mb-1 text-left"
            >
              OCR Confidence
            </label>
            <select
              id="confidence-filter"
              className="w-full px-3 py-1.5 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
              value={filters.confidence}
              onChange={(e) => handleFilterChange('confidence', e.target.value)}
            >
              <option value="">All Levels</option>
              <option value="high">Very High (90–100%)</option>
              <option value="medium">High (70–89%)</option>
              <option value="low">Medium (50–69%)</option>
              <option value="very-low">Low (&lt;50%)</option>
            </select>
          </div>

          {/* Status */}
          <div>
            <label
              htmlFor="status-filter"
              className="block text-sm text-museum-700 mb-1 font-bold text-left"
            >
              Status
            </label>
            <select
              id="status-filter"
              className="w-full px-3 py-1.5 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
              value={filters.status}
              onChange={(e) => handleFilterChange('status', e.target.value)}
            >
              <option value="">All Statuses</option>
              <option value="pending">Pending</option>
              <option value="reviewed">Reviewed</option>
              <option value="published">Published</option>
              <option value="publishing">Publishing</option>
            </select>
          </div>
        </div>
      </div>
    </section>
  )
}

export default SearchFilters
