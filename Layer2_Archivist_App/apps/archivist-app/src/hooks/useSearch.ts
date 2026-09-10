'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { FilterState, ArchivalRecord } from '@/types'
import { mockRecords } from '@/data/mockData'
import { equalsIgnoreCase } from '@/utils/textCompare'

export const useSearch = (initialFilters: FilterState, records: ArchivalRecord[] = mockRecords) => {
  const [filters, setFilters] = useState<FilterState>(initialFilters)
  const [isSearching, setIsSearching] = useState(false)
  const [filteredRecords, setFilteredRecords] = useState(records)
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Debounced search for the title field
  const debouncedSearch = useCallback((searchValue: string, delay: number = 300) => {
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current)

    searchTimeoutRef.current = setTimeout(() => {
      setFilters(prev => ({ ...prev, search: searchValue }))
      setIsSearching(false)
    }, delay)
  }, [])

  const handleSearch = useCallback(
    (searchValue: string) => {
      setIsSearching(true)
      debouncedSearch(searchValue)
    },
    [debouncedSearch]
  )

  const handleFiltersChange = useCallback((newFilters: Partial<FilterState>) => {
    setFilters(prev => ({ ...prev, ...newFilters }))
  }, [])

  const clearFilters = useCallback(() => setFilters(initialFilters), [initialFilters])
  const resetSearch = useCallback(() => setFilters(prev => ({ ...prev, search: '' })), [])

  // Apply all filters to the records
  useEffect(() => {
    let filteredData = [...records]

    // Search by title
    if (filters.search.trim()) {
      const searchTerm = filters.search.toLowerCase()
      filteredData = filteredData.filter(r => r.title.toLowerCase().includes(searchTerm))
    }

    // Filter by repository
    if (filters.repository) {
      filteredData = filteredData.filter(r =>
        equalsIgnoreCase(r.source.repository, filters.repository)
      )
    }

    // Filter by collection
    if (filters.collection) {
      filteredData = filteredData.filter(r =>
        equalsIgnoreCase(r.source.collection, filters.collection)
      )
    }

    // Filter by status
    if (filters.status) {
      filteredData = filteredData.filter(r => r.status === filters.status)
    }

    // Filter by confidence
    if (filters.confidence) {
      filteredData = filteredData.filter(r => {
        const conf = r.aiConfidence
        switch (filters.confidence) {
          case 'high':
            return conf >= 90
          case 'medium':
            return conf >= 70 && conf < 90
          case 'low':
            return conf >= 50 && conf < 70
          case 'very-low':
            return conf < 50
          default:
            return true
        }
      })
    }

    // Filter by date range
    if (filters.dateRange.start) {
      filteredData = filteredData.filter(r => new Date(r.date) >= new Date(filters.dateRange.start))
    }
    if (filters.dateRange.end) {
      filteredData = filteredData.filter(r => new Date(r.date) <= new Date(filters.dateRange.end))
    }

    setFilteredRecords(filteredData)
  }, [filters, records])

  // Cleanup timeout when component unmounts
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current)
    }
  }, [])

  return {
    filters,
    isSearching,
    filteredRecords,
    handleSearch,
    handleFiltersChange,
    clearFilters,
    resetSearch
  }
}
