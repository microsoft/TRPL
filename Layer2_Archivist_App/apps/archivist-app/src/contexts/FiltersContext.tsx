import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react'

interface FiltersContextType {
  searchQuery: string
  typeFilter: string
  sourceFilter: string
  creatorFilter: string
  recipientFilter: string
  confidenceFilter: string
  statusFilter: string
  repository: string
  collectionName: string
  sortBy: string
  currentPage: number
  currentRecordIndex: number
  setSearchQuery: (value: string) => void
  setTypeFilter: (value: string) => void
  setSourceFilter: (value: string) => void
  setCreatorFilter: (value: string) => void
  setRecipientFilter: (value: string) => void
  setConfidenceFilter: (value: string) => void
  setStatusFilter: (value: string) => void
  setRepository: (value: string) => void
  setCollectionName: (value: string) => void
  setSortBy: (value: string) => void
  setCurrentPage: (value: number) => void
  setCurrentRecordIndex: (value: number) => void
}

const FiltersContext = createContext<FiltersContextType | undefined>(undefined)

const STORAGE_KEY = 'collectionFilters'

interface FiltersProviderProps {
  children: ReactNode
  initialRepository?: string
  initialCollectionName?: string
}

// Load filters from sessionStorage
const loadFiltersFromStorage = () => {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY)
    if (stored) {
      return JSON.parse(stored)
    }
  } catch (error) {
    console.error('Failed to load filters from storage:', error)
  }
  return null
}

// Save filters to sessionStorage
const saveFiltersToStorage = (filters: {
  searchQuery: string
  typeFilter: string
  sourceFilter: string
  creatorFilter: string
  recipientFilter: string
  confidenceFilter: string
  statusFilter: string
  repository: string
  collectionName: string
  sortBy: string
  currentPage: number
  currentRecordIndex: number
}) => {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filters))
  } catch (error) {
    console.error('Failed to save filters to storage:', error)
  }
}

export const FiltersProvider: React.FC<FiltersProviderProps> = ({ 
  children, 
  initialRepository = '',
  initialCollectionName = ''
}) => {
  // Load from storage or use initial values
  const storedFilters = loadFiltersFromStorage()
  
  // Initialize state: prefer stored values, but use initial values for repository/collection if provided
  const [searchQuery, setSearchQueryState] = useState(storedFilters?.searchQuery || '')
  const [typeFilter, setTypeFilterState] = useState(storedFilters?.typeFilter || '')
  const [sourceFilter, setSourceFilterState] = useState(storedFilters?.sourceFilter || '')
  const [creatorFilter, setCreatorFilterState] = useState(storedFilters?.creatorFilter || '')
  const [recipientFilter, setRecipientFilterState] = useState(storedFilters?.recipientFilter || '')
  const [confidenceFilter, setConfidenceFilterState] = useState(storedFilters?.confidenceFilter || '')
  const [statusFilter, setStatusFilterState] = useState(storedFilters?.statusFilter || '')
  const [repository, setRepositoryState] = useState(initialRepository || storedFilters?.repository || '')
  const [collectionName, setCollectionNameState] = useState(initialCollectionName || storedFilters?.collectionName || '')
  const [sortBy, setSortByState] = useState(storedFilters?.sortBy || 'Newest first')
  const [currentPage, setCurrentPageState] = useState(storedFilters?.currentPage || 1)
  const [currentRecordIndex, setCurrentRecordIndexState] = useState(storedFilters?.currentRecordIndex || -1)

  // Update repository/collection when URL params change (but only if they're different)
  useEffect(() => {
    if (initialRepository && initialRepository !== repository) {
      setRepositoryState(initialRepository)
    }
  }, [initialRepository, repository])

  useEffect(() => {
    if (initialCollectionName && initialCollectionName !== collectionName) {
      setCollectionNameState(initialCollectionName)
    }
  }, [initialCollectionName, collectionName])

  // Save to storage whenever filters change
  useEffect(() => {
    saveFiltersToStorage({
      searchQuery,
      typeFilter,
      sourceFilter,
      creatorFilter,
      recipientFilter,
      confidenceFilter,
      statusFilter,
      repository,
      collectionName,
      sortBy,
      currentPage,
      currentRecordIndex
    })
  }, [searchQuery, typeFilter, sourceFilter, creatorFilter, recipientFilter, confidenceFilter, statusFilter, repository, collectionName, sortBy, currentPage, currentRecordIndex])

  // Wrapper functions that update state and storage
  const setSearchQuery = (value: string) => {
    setSearchQueryState(value)
  }

  const setTypeFilter = (value: string) => {
    setTypeFilterState(value)
  }

  const setSourceFilter = (value: string) => {
    setSourceFilterState(value)
  }

  const setCreatorFilter = (value: string) => {
    setCreatorFilterState(value)
  }

  const setRecipientFilter = (value: string) => {
    setRecipientFilterState(value)
  }

  const setConfidenceFilter = (value: string) => {
    setConfidenceFilterState(value)
  }

  const setStatusFilter = (value: string) => {
    setStatusFilterState(value)
  }

  const setRepository = (value: string) => {
    setRepositoryState(value)
  }

  const setCollectionName = (value: string) => {
    setCollectionNameState(value)
  }

  const setSortBy = (value: string) => {
    setSortByState(value)
  }

  const setCurrentPage = (value: number) => {
    setCurrentPageState(value)
  }

  const setCurrentRecordIndex = (value: number) => {
    setCurrentRecordIndexState(value)
  }

  const value: FiltersContextType = {
    searchQuery,
    typeFilter,
    sourceFilter,
    creatorFilter,
    recipientFilter,
    confidenceFilter,
    statusFilter,
    repository,
    collectionName,
    sortBy,
    currentPage,
    currentRecordIndex,
    setSearchQuery,
    setTypeFilter,
    setSourceFilter,
    setCreatorFilter,
    setRecipientFilter,
    setConfidenceFilter,
    setStatusFilter,
    setRepository,
    setCollectionName,
    setSortBy,
    setCurrentPage,
    setCurrentRecordIndex,
  }

  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>
}

export const useFilters = (): FiltersContextType => {
  const context = useContext(FiltersContext)
  if (context === undefined) {
    throw new Error('useFilters must be used within a FiltersProvider')
  }
  return context
}

