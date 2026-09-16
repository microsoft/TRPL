// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import Header from '@/components/Header'
import PageHeader from '@/components/PageHeader'
import SearchFilters from '@/components/SearchFilters'
import TableControls from '@/components/TableControls'
import RecordsTable from '@/components/RecordsTable'
import Pagination from '@/components/Pagination'
import InfoSidebar from '@/components/InfoSidebar'
import QuickActions from '@/components/QuickActions'
import KeyboardShortcutsModal from '@/components/KeyboardShortcutsModal'
import Toast from '@/components/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'
import MissingTrcExcludedDialog from '@/components/MissingTrcExcludedDialog'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { mockStats, mockRecords } from '@/data/mockData'
import { apiService } from '@/services/api'
import { mapApiDocumentsToRecords } from '@/utils/mapApiToRecord'
import { equalsIgnoreCase } from '@/utils/textCompare'
import { hasPortalPublishDateMetadata } from '@/utils/portalPublishDate'
import {
  FilterState,
  PaginationState,
  SortState,
  UIState,
  BulkActionState,
  ArchivalRecord
} from '@/types'

// SessionStorage key for state persistence
const STATE_STORAGE_KEY = 'homePageState'

// Helper functions for state persistence
const saveStateToSession = (state: {
  filters: FilterState
  pagination: PaginationState
  sort: SortState
  scrollPosition: number
}) => {
  try {
    sessionStorage.setItem(STATE_STORAGE_KEY, JSON.stringify(state))
  } catch (error) {
    console.error('Failed to save state:', error)
  }
}

const loadStateFromSession = (): {
  filters: FilterState
  pagination: PaginationState
  sort: SortState
  scrollPosition: number
} | null => {
  try {
    const saved = sessionStorage.getItem(STATE_STORAGE_KEY)
    return saved ? JSON.parse(saved) : null
  } catch (error) {
    console.error('Failed to load state:', error)
    return null
  }
}

const initialFilters: FilterState = {
  search: '',
  repository: '',
  collection: '',
  resourceType: '',
  status: '',
  dateRange: {
    start: '',
    end: ''
  },
  confidence: ''
}

const initialPagination: PaginationState = {
  currentPage: 1,
  itemsPerPage: 20,
  totalItems: 0,
  totalPages: 0
}

const initialSort: SortState = {
  field: 'created_at',
  direction: 'desc'
}

const initialUI: UIState = {
  sidebarOpen: false,
  shortcutsModalOpen: false,
  viewMode: 'table',
  bulkActionsVisible: false
}

const initialBulkActions: BulkActionState = {
  selectedItems: [],
  isVisible: false
}

export default function HomePage() {
  const navigate = useNavigate()
  const location = useLocation()
  const isInitialMount = useRef(true)
  const hasRestoredState = useRef(false)
  const isApplyingQueryParams = useRef(false)

  // Function to get initial state from sessionStorage or location state
  const getInitialState = () => {
    // First, try to load from sessionStorage
    const savedState = loadStateFromSession()
    
    if (savedState) {
      hasRestoredState.current = true
      return {
        filters: savedState.filters,
        pagination: savedState.pagination,
        sort: savedState.sort,
        scrollPosition: savedState.scrollPosition
      }
    }

    // Second, check URL query params (prefer them) then fall back to location.state
    // This supports navigation from CollectionsGrid which sets both state and query params.
    const searchParams = new URLSearchParams(location.search || window.location.search)
    const repoFromQuery = searchParams.get('repository')
    const colFromQuery = searchParams.get('collection')
    // Debug: log parsed query params
    // console.log('Parsed query params for filters:', { repoFromQuery, colFromQuery })
    if (repoFromQuery || colFromQuery) {
      return {
        filters: {
          ...initialFilters,
          repository: repoFromQuery || '',
          collection: colFromQuery || ''
        },
        pagination: initialPagination,
        sort: initialSort,
        scrollPosition: 0
      }
    }

    // Third, check if there are filters from location state (e.g., from in-app navigation)
    const locationState = location.state as { filters?: { repository?: string; collection?: string } } | null

    if (locationState?.filters) {
      return {
        filters: {
          ...initialFilters,
          repository: locationState.filters.repository || '',
          collection: locationState.filters.collection || ''
        },
        pagination: initialPagination,
        sort: initialSort,
        scrollPosition: 0
      }
    }

    // Default initial state
    return {
      filters: initialFilters,
      pagination: initialPagination,
      sort: initialSort,
      scrollPosition: 0
    }
  }

  const initialStateData = getInitialState()

  const [filters, setFilters] = useState<FilterState>(initialStateData.filters)

  const [records, setRecords] = useState<ArchivalRecord[]>([])

  
  const [totalCount, setTotalCount] = useState<number>(0)
  const [pagination, setPagination] = useState<PaginationState>(initialStateData.pagination)
  const [sort, setSort] = useState<SortState>(initialStateData.sort)
  const [ui, setUI] = useState<UIState>(initialUI)
  const [bulkActions, setBulkActions] = useState<BulkActionState>(initialBulkActions)
  const [toast, setToast] = useState<{
    type: 'success' | 'error' | 'warning' | 'info'
    title: string
    message: string
  } | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [isPaginating, setIsPaginating] = useState<boolean>(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [repositories, setRepositories] = useState<string[]>([])
  const [reposLoading, setReposLoading] = useState<boolean>(false)
  const [collections, setCollections] = useState<string[]>([])
  const [collectionsLoading, setCollectionsLoading] = useState<boolean>(false)
  const [resourceTypes, setResourceTypes] = useState<string[]>([])
  const [resourceTypesLoading, setResourceTypesLoading] = useState<boolean>(false)
  const savedScrollPosition = useRef<number>(initialStateData.scrollPosition)

  // Save state to sessionStorage whenever key values change (but not on initial mount)
  useEffect(() => {
    if (isInitialMount.current) return
    
    const stateToSave = {
      filters,
      pagination,
      sort,
      scrollPosition: window.scrollY
    }
    
    // Debounce the save to avoid too many writes
    const timeoutId = setTimeout(() => {
      saveStateToSession(stateToSave)
    }, 300)
    
    return () => clearTimeout(timeoutId)
  }, [filters, pagination, sort])

  // Restore scroll position after records are loaded
  useEffect(() => {
    if (!isLoading && records.length > 0 && hasRestoredState.current && savedScrollPosition.current > 0) {
      const restoreScroll = () => {
        window.scrollTo({
          top: savedScrollPosition.current,
          behavior: 'auto'
        })
      }

      // Multiple attempts to ensure scroll restoration works
      restoreScroll()
      setTimeout(restoreScroll, 100)
      setTimeout(restoreScroll, 300)

      const finalTimeout = setTimeout(() => {
        restoreScroll()
        hasRestoredState.current = false
        savedScrollPosition.current = 0
      }, 500)

      return () => clearTimeout(finalTimeout)
    }
  }, [isLoading, records.length])

  // Clear location state after reading it
  useEffect(() => {
    if (location.state?.filters) {
      // Clear the location state after we've read it
      // Preserve any query params when replacing the location so we don't remove filters
      navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: {} })
    }
  }, []) // Only run once on mount

  // Update pagination based on API response
  useEffect(() => {
    if (totalCount === 0) return;

    const totalPages = Math.ceil(totalCount / pagination.itemsPerPage);

    setPagination((prev) => ({
      ...prev,
      totalItems: totalCount,
      totalPages,
    }));
  }, [totalCount, pagination.itemsPerPage]);

  // Fetch documents from API
  const fetchDocuments = useCallback(
    async (opts?: {
      items_per_page?: number;
      page_number?: number;
    }) => {

      try {
        setIsLoading(true)
        setApiError(null)

        const params: any = {
          page_size: opts?.items_per_page || pagination.itemsPerPage,
          page_number: opts?.page_number || pagination.currentPage || 1,
        }

        params.order_by = sort.field || 'created_at'
        params.descending = sort.direction === 'desc'
        // Pass current filters to API (only include non-empty values)
        if (filters.status) params.status = filters.status
        if (filters.repository) params.repository = filters.repository
        if (filters.collection) params.collection = filters.collection
        if (filters.resourceType) params.resource_type = filters.resourceType
        if (filters.search) params.title_contains = filters.search

        // Map confidence filter to numeric ranges
        if (filters.confidence) {
          switch (filters.confidence) {
            case 'high':
              params.min_confidence = 0.90
              params.max_confidence = 1.0
              break
            case 'medium':
              params.min_confidence = 0.70
              params.max_confidence = 0.89
              break
            case 'low':
              params.min_confidence = 0.50
              params.max_confidence = 0.69
              break
            case 'very-low':
              params.min_confidence = 0.0
              params.max_confidence = 0.49
              break
            default:
              // 'all' or empty - no confidence filter
              break
          }


        }

        const response = await apiService.getDocuments(params)
        console.log('API params sent:', params)  // Add this line
        console.log('API response:', response)
        console.log('First document metadata:', response.documents[0]?.metadata)

        const mappedRecords = mapApiDocumentsToRecords(response.documents)

        console.log('Mapped Records:', mappedRecords)  // Add this line
        console.log('Sample documentId values:', mappedRecords.slice(0, 3).map(r => ({ 
          title: r.title, 
          documentId: r.documentId,
          collection: r.source.collection 
        })))
        setRecords(mappedRecords)

        const count = response.count || 0;
        setTotalCount(count);

        console.log(
          "Loaded documents: %d",
          mappedRecords.length
        );
        return count;

      } catch (error) {
        console.error('Failed to fetch documents:', error)
        setApiError('Failed to load documents from API. Using mock data.')
        // Fallback to mock data
        setRecords(mockRecords)
        const mockCount = mockRecords.length
        setTotalCount(mockCount)
        setToast({
          type: 'warning',
          title: 'API Connection Issue',
          message: 'Could not connect to API. Displaying sample data.'
        })
        return mockCount
      } finally {
        setIsLoading(false)
        setIsPaginating(false)
      }
    }, [filters, pagination.itemsPerPage, pagination.currentPage, sort])

  // Initial load - skip if query params are present (will be handled by query param effect)
  useEffect(() => {
    const searchParams = new URLSearchParams(location.search || window.location.search)
    const hasQueryParams = searchParams.has('repository') || searchParams.has('collection')
    
    // Only fetch if no query params (query param effect will handle those)
    if (!hasQueryParams) {
      fetchDocuments()
    }
  }, [])

  // If the page was opened with query params (or navigated from CollectionsGrid),
  // apply those filters and run a search so the initial results reflect them.
  // NOTE: this effect is placed after `handleSearchSubmit` declaration to avoid
  // "used before declaration" linter errors.

  // Fetch collection summaries and extract unique repository/collection values
  // This matches the data source used by Collections.tsx so dropdowns are consistent
  useEffect(() => {
    let cancelled = false
    const loadCollectionSummaries = async () => {
      setReposLoading(true)
      try {
        const resp = await apiService.getCollectionSummaries()
        if (!cancelled) {
          // Extract unique repositories and collections from summaries
          const uniqueRepos = Array.from(
            new Set(resp.summaries.map((s: any) => s.repository).filter(Boolean))
          ).sort()
          const uniqueCols = Array.from(
            new Set(resp.summaries.map((s: any) => s.collection).filter(Boolean))
          ).sort()
          setRepositories(uniqueRepos)
          setCollections(uniqueCols)
        }
      } catch (err) {
        console.error('Failed to load collection summaries:', err)
      } finally {
        if (!cancelled) setReposLoading(false)
      }
    }
    loadCollectionSummaries()
    return () => { cancelled = true }
  }, [])

  // Fetch collections whenever repository selection changes
  useEffect(() => {
    let cancelled = false
    const loadCollections = async () => {
      setCollectionsLoading(true)
      try {
        const resp = await apiService.getCollectionSummaries()
        if (!cancelled) {
          // If a repository is selected, filter collections to that repository
          let filteredCollections = resp.summaries
            .filter(
              (s: any) =>
                !filters.repository || equalsIgnoreCase(s.repository, filters.repository)
            )
            .map((s: any) => s.collection)
            .filter(Boolean)
          // Remove duplicates and sort
          filteredCollections = Array.from(new Set(filteredCollections)).sort()
          setCollections(filteredCollections)
        }
      } catch (err) {
        console.error('Failed to load collections:', err)
      } finally {
        if (!cancelled) setCollectionsLoading(false)
      }
    }
    loadCollections()
    return () => { cancelled = true }
  }, [filters.repository])

  // Fetch resource types on mount
  useEffect(() => {
    let cancelled = false
    const loadResourceTypes = async () => {
      setResourceTypesLoading(true)
      try {
        const resp = await apiService.getResourceTypes()
        if (!cancelled) setResourceTypes(resp.resource_types || [])
      } catch (err) {
        console.error('Failed to load resource types:', err)
      } finally {
        if (!cancelled) setResourceTypesLoading(false)
      }
    }
    loadResourceTypes()
    return () => { cancelled = true }
  }, [])

  // Re-fetch when key filters change (status, repository, collection, resourceType)
  useEffect(() => {
    // Don't refetch on initial mount or when applying query params
    if (isInitialMount.current || isApplyingQueryParams.current) {
      isInitialMount.current = false
      return
    }
    
    setPagination(prev => ({ ...prev, currentPage: 1 }))
    fetchDocuments({ page_number: 1, items_per_page: pagination.itemsPerPage })
  }, [filters.status, filters.repository, filters.collection, filters.resourceType, filters.confidence])

  const handleSearchSubmit = useCallback(async (updatedFilters: FilterState, isFromQueryParams: boolean = false) => {
    try {
      setFilters(updatedFilters);

      setPagination(prev => ({ ...prev, currentPage: 1 }))

      const params: any = {
        page_number: 1,
        page_size: pagination.itemsPerPage,
        order_by: sort.field || "created_at",
        descending: sort.direction === "desc",
      };

      if (updatedFilters.status) params.status = updatedFilters.status;
      if (updatedFilters.repository) params.repository = updatedFilters.repository;
      if (updatedFilters.collection) params.collection = updatedFilters.collection;
      if (updatedFilters.resourceType) params.resource_type = updatedFilters.resourceType;
      if (updatedFilters.search) params.title_contains = updatedFilters.search;

      if (updatedFilters.confidence) {
        switch (updatedFilters.confidence) {
          case "high":
            params.min_confidence = 0.9;
            params.max_confidence = 1.0;
            break;
          case "medium":
            params.min_confidence = 0.7;
            params.max_confidence = 0.89;
            break;
          case "low":
            params.min_confidence = 0.5;
            params.max_confidence = 0.69;
            break;
          case "very-low":
            params.min_confidence = 0.0;
            params.max_confidence = 0.49;
            break;
        }
      }

      setIsLoading(true);
      const response = await apiService.getDocuments(params);
      const mappedRecords = mapApiDocumentsToRecords(response.documents);

      setRecords(mappedRecords);

      const count = response.count || 0;
      setTotalCount(count);

      // Only show toast if filters weren't from Collections page (query params)
      if (!isFromQueryParams) {
        setToast({
          type: 'success',
          title: 'Search completed',
          message: `Found ${count} item(s) matching your criteria`
        })
      }
    } catch (error) {
      setToast({
        type: "error",
        title: "Search failed",
        message: "Unable to complete search. Please try again.",
      });
    } finally {
      setIsLoading(false);
    }
  }, [pagination.itemsPerPage, sort]);

  // If the page was opened with query params (or navigated from CollectionsGrid),
  // apply those filters and run a search so the initial results reflect them.
  useEffect(() => {
    const searchParams = new URLSearchParams(location.search || window.location.search)
    const repo = searchParams.get('repository')
    const col = searchParams.get('collection')
    if (repo || col) {
      const updatedFilters = {
        ...initialFilters,
        repository: repo || '',
        collection: col || ''
      }
      isApplyingQueryParams.current = true
      // Ensure spinner is visible while loading
      setIsLoading(true)
      // Use the existing handler which will set state and fetch
      // Pass true to indicate filters came from query params so we skip the success toast
      void handleSearchSubmit(updatedFilters, true).finally(() => {
        // Reset flag after search completes
        isApplyingQueryParams.current = false
      })
    }
  }, [])

  // Handle page change with proper data fetching
  const handlePageChange = useCallback(
    async (targetPage: number) => {
      if (isPaginating || targetPage === pagination.currentPage) return;


      // We need to fetch more data
      setIsPaginating(true);

      try {
        await fetchDocuments({ page_number: targetPage, items_per_page: pagination.itemsPerPage });
        // Now update the page
        setPagination((prev) => ({ ...prev, currentPage: targetPage }));
        window.scrollTo({ top: 0, behavior: "smooth" });

      } catch (error) {
        console.error("Error during page navigation:", error);
        setToast({
          type: "error",
          title: "Navigation Failed",
          message: "Could not navigate to the requested page",
        });
      } finally {
        setIsPaginating(false);
      }
    },
    [
      isPaginating,
      pagination.currentPage,
      pagination.itemsPerPage,
      fetchDocuments
    ]
  );

  const handlePaginationChange = useCallback(
    async (newPagination: PaginationState) => {
      const itemsPerPageChanged =
        newPagination.itemsPerPage !== pagination.itemsPerPage;

      if (itemsPerPageChanged) {
        // Reset continuation state and load fresh results using the updated pagination.itemsPerPage
        setPagination((prev) => ({
          ...prev,
          itemsPerPage: newPagination.itemsPerPage,
          currentPage: 1,
        }))
        await fetchDocuments({ page_number: 1, items_per_page: newPagination.itemsPerPage })
        return
      }

      // Just update the current page
      setPagination((prev) => ({
        ...prev,
        currentPage: newPagination.currentPage,
      }));
    },
    [pagination.itemsPerPage, fetchDocuments]
  );

  const handleSortChange = useCallback(async (newSort: SortState) => {
    setSort(newSort)
    // Reset to first page and fetch with new sort order
    setPagination(prev => ({ ...prev, currentPage: 1 }))
    
    // Fetch documents with new sort order
    const params: any = {
      page_number: 1,
      page_size: pagination.itemsPerPage,
      order_by: newSort.field || 'created_at',
      descending: newSort.direction === 'desc',
    }

    if (filters.status) params.status = filters.status
    if (filters.repository) params.repository = filters.repository
    if (filters.collection) params.collection = filters.collection
    if (filters.resourceType) params.resource_type = filters.resourceType
    if (filters.search) params.title_contains = filters.search

    if (filters.confidence) {
      switch (filters.confidence) {
        case 'high':
          params.min_confidence = 0.90
          params.max_confidence = 1.0
          break
        case 'medium':
          params.min_confidence = 0.70
          params.max_confidence = 0.89
          break
        case 'low':
          params.min_confidence = 0.50
          params.max_confidence = 0.69
          break
        case 'very-low':
          params.min_confidence = 0.0
          params.max_confidence = 0.49
          break
      }
    }

    try {
      setIsLoading(true)
      const response = await apiService.getDocuments(params)
      const mappedRecords = mapApiDocumentsToRecords(response.documents)
      setRecords(mappedRecords)
      const count = response.count || 0
      setTotalCount(count)
    } catch (error) {
      console.error('Failed to fetch sorted documents:', error)
      setToast({
        type: 'error',
        title: 'Sort Failed',
        message: 'Unable to apply sorting. Please try again.'
      })
    } finally {
      setIsLoading(false)
    }
  }, [filters, pagination.itemsPerPage])

  const handleViewModeChange = useCallback((viewMode: UIState['viewMode']) => {
    setUI(prev => ({ ...prev, viewMode }))
  }, [])

  const handleRecordClick = useCallback((record: ArchivalRecord) => {
    // Save current scroll position before navigating
    const currentScrollY = window.scrollY
    const stateToSave = {
      filters,
      pagination,
      sort,
      scrollPosition: currentScrollY
    }
    saveStateToSession(stateToSave)
    
    navigate(`/review/${record.id}`)
  }, [navigate, filters, pagination, sort])

  const [isIngesting, setIsIngesting] = useState(false)
  const [isIngestingByQuery, setIsIngestingByQuery] = useState(false)
  const [showIngestConfirm, setShowIngestConfirm] = useState(false)
  const [showIngestByQueryConfirm, setShowIngestByQueryConfirm] = useState(false)
  const [missingTrcDialog, setMissingTrcDialog] = useState<{
    total: number
    ids: string[]
    truncated: boolean
  } | null>(null)

  const handleBulkSelectionChange = useCallback((selectedIds: string[]) => {
    setBulkActions(prev => ({
      ...prev,
      selectedItems: selectedIds,
      isVisible: selectedIds.length > 0
    }))
  }, [])

  const handleIngestClick = useCallback(() => {
    if (bulkActions.selectedItems.length === 0) {
      setToast({
        type: 'error',
        title: 'No Selection',
        message: 'Please select at least one document to ingest'
      })
      return
    }
    setShowIngestConfirm(true)
  }, [bulkActions.selectedItems.length])

  const handleConfirmIngest = useCallback(async () => {
    const recordIds = bulkActions.selectedItems
    const withTrc: string[] = []
    const lackingTrc: string[] = []
    for (const rid of recordIds) {
      const rec = records.find((r) => r.id === rid)
      const raw = rec?.rawMetadata?.['Date Published to Portal']
      if (hasPortalPublishDateMetadata(raw)) {
        withTrc.push(rid)
      } else {
        lackingTrc.push(rid)
      }
    }
    if (withTrc.length === 0) {
      setToast({
        type: 'error',
        title: 'Cannot publish',
        message:
          'None of the selected records have a usable Date Published to Portal value. Add one, then try again.',
      })
      setShowIngestConfirm(false)
      return
    }

    try {
      setIsIngesting(true)
      const result = await apiService.ingestDocuments(withTrc)

      if (lackingTrc.length > 0) {
        const maxIds = 300
        setMissingTrcDialog({
          total: lackingTrc.length,
          ids: lackingTrc.slice(0, maxIds),
          truncated: lackingTrc.length > maxIds,
        })
      }

      setToast({
        type: 'success',
        title: 'Ingestion Queued',
        message:
          result.message ||
          (lackingTrc.length > 0
            ? `Queued ${withTrc.length} document(s) with TRC. ${lackingTrc.length} selected without TRC were skipped.`
            : `Successfully queued ${withTrc.length} document(s) for ingestion`),
      })

      // Clear selection after successful ingest
      handleBulkSelectionChange([])
    } catch (error) {
      console.error('Failed to ingest documents:', error)
      setToast({
        type: 'error',
        title: 'Ingestion Failed',
        message: error instanceof Error ? error.message : 'Failed to queue documents for ingestion'
      })
    } finally {
      setIsIngesting(false)
      setShowIngestConfirm(false)
    }
  }, [bulkActions.selectedItems, handleBulkSelectionChange, records])

  // Build query parameters for ingest by query
  const buildIngestQueryParams = useCallback(() => {
    const queryParams: {
      status?: string;
      repository?: string;
      collection?: string;
      resource_type?: string;
      min_confidence?: number;
      max_confidence?: number;
      title_contains?: string;
    } = {}

    if (filters.status) queryParams.status = filters.status
    if (filters.repository) queryParams.repository = filters.repository
    if (filters.collection) queryParams.collection = filters.collection
    if (filters.resourceType) queryParams.resource_type = filters.resourceType
    if (filters.search) queryParams.title_contains = filters.search

    // Map confidence filter to numeric ranges
    if (filters.confidence) {
      switch (filters.confidence) {
        case 'high':
          queryParams.min_confidence = 0.90
          queryParams.max_confidence = 1.0
          break
        case 'medium':
          queryParams.min_confidence = 0.70
          queryParams.max_confidence = 0.89
          break
        case 'low':
          queryParams.min_confidence = 0.50
          queryParams.max_confidence = 0.69
          break
        case 'very-low':
          queryParams.min_confidence = 0.0
          queryParams.max_confidence = 0.49
          break
      }
    }
    return queryParams
  }, [filters])

  const handleIngestByQueryClick = useCallback(() => {
    const queryParams = buildIngestQueryParams()
    const hasFilters = Object.keys(queryParams).length > 0

    if (!hasFilters) {
      setToast({
        type: 'error',
        title: 'No Filters',
        message: 'Please apply at least one filter before ingesting by query'
      })
      return
    }
    setShowIngestByQueryConfirm(true)
  }, [buildIngestQueryParams])

  const handleConfirmIngestByQuery = useCallback(async () => {
    const queryParams = buildIngestQueryParams()

    try {
      setIsIngestingByQuery(true)
      const result = await apiService.ingestDocumentsByQuery(queryParams)

      const trcTotal = result.excluded_missing_trc_total ?? 0
      if (trcTotal > 0) {
        setMissingTrcDialog({
          total: trcTotal,
          ids: result.excluded_missing_trc_record_ids ?? [],
          truncated: Boolean(result.excluded_missing_trc_ids_truncated),
        })
      }

      setToast({
        type: 'success',
        title: 'Ingestion Queued',
        message: result.message || 'Successfully queued documents matching the filters for ingestion'
      })
    } catch (error) {
      console.error('Failed to ingest documents by query:', error)
      setToast({
        type: 'error',
        title: 'Ingestion Failed',
        message: error instanceof Error ? error.message : 'Failed to queue documents for ingestion'
      })
    } finally {
      setIsIngestingByQuery(false)
      setShowIngestByQueryConfirm(false)
    }
  }, [filters])

  const handleBulkAction = useCallback((action: string) => {
    console.log('Bulk action:', action, bulkActions.selectedItems)
    // TODO: Implement bulk actions
  }, [bulkActions.selectedItems])

  const handleSelectAll = useCallback(() => {
    const allIds = records.map((record) => record.id);
    handleBulkSelectionChange(allIds);
  }, [handleBulkSelectionChange, records]);

  const handleClearSelection = useCallback(() => {
    handleBulkSelectionChange([]);
  }, [handleBulkSelectionChange]);

  const handleSidebarToggle = useCallback(() => {
    setUI((prev) => ({ ...prev, sidebarOpen: !prev.sidebarOpen }));
  }, []);

  const handleDownloadClick = useCallback(() => {
    console.log('Download clicked')
    // TODO: Implement download functionality
  }, [])

  const handleAddClick = useCallback(() => {
    console.log('Add clicked')
    // TODO: Implement add functionality
  }, [])

  const handleToastClose = useCallback(() => {
    setToast(null)
  }, [])

  const handleFiltersChange = useCallback((newFilters: Partial<FilterState>) => {
    setFilters(prev => ({ ...prev, ...newFilters }))
  }, [])


  // Keyboard shortcuts
  useKeyboardShortcuts({
    onSearch: () => {
      const searchInput = document.querySelector(
        'input[type="text"]'
      ) as HTMLInputElement;
      searchInput?.focus()
    },
    onSelectAll: handleSelectAll,
    onExport: () => handleBulkAction('export'),
    onOpenItem: () => {
      // Open first pending record
      const firstPending = records.find(record => record.status === 'pending')
      if (firstPending) {
        handleRecordClick(firstPending)
      }
    },
    onEscape: () => {
      setUI(prev => ({
        ...prev,
        sidebarOpen: false,
        shortcutsModalOpen: false
      }));
    },
  });

  function handleSearch(): void {
    fetchDocuments();
  }

  return (
    <div className="min-h-screen bg-white-500 ">
      <Header
        currentPage="home"
        onNotificationClick={() => { }}
        onHelpClick={() => setUI(prev => ({ ...prev, shortcutsModalOpen: true }))}
        onUserMenuClick={() => { }}
      />
      {/* <BreadcrumbNav/> */}
      <PageHeader
        title={
          <span className="text-2xl font-semibold text-museum-900 mb-2">
            Review Queue
          </span>
        }
        description="Items requiring archivist validation and correction"
        stats={mockStats}
      />

      {/* Loading State */}
      {isLoading && (
        <div className="flex justify-center items-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-museum-600"></div>
          <span className="ml-4 text-museum-600">Loading documents...</span>
        </div>
      )}

      {/* Error State */}
      {apiError && (
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-4">
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-yellow-800">{apiError}</p>
          </div>
        </div>
      )}

      {/* Content - only show when not loading */}
      {!isLoading && (
        <>
          <SearchFilters
            filters={filters}
            onFiltersChange={handleFiltersChange}
            onSearch={handleSearchSubmit}
            repositories={repositories}
            collections={collections}
            resourceTypes={resourceTypes}
            onAdvancedFilters={function (): void {
              throw new Error('Function not implemented.')
            }} />
          <TableControls
            pagination={pagination}
            sort={sort}
            onPaginationChange={handlePaginationChange}
            onSortChange={handleSortChange}
            onIngestClick={handleIngestClick}
            selectedCount={bulkActions.selectedItems.length}
            isIngesting={isIngesting}
            onIngestByQueryClick={handleIngestByQueryClick}
            isIngestingByQuery={isIngestingByQuery}
          />
          <RecordsTable
            records={records}
            bulkActions={bulkActions}
            onRecordClick={handleRecordClick}
            onBulkSelectionChange={handleBulkSelectionChange}
          />
          <div className="relative">
            {isPaginating && (
              <div className="absolute inset-0 bg-white bg-opacity-75 flex justify-center items-center z-10">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-museum-600"></div>
                <span className="ml-3 text-museum-600 font-medium">Loading more records...</span>
              </div>
            )}

            {pagination.totalItems > 0 ? (
              <Pagination
                pagination={pagination}
                isLoading={isPaginating}
                onPageChange={handlePageChange}
                onItemsPerPageChange={(newItemsPerPage) => {
                  setPagination((prev) => ({
                    ...prev,
                    itemsPerPage: newItemsPerPage,
                    currentPage: 1,
                  }));
                }}
              />
            ) : (
              <div className='p-5'>No data found</div>
            )}
          </div>
        </>
      )}

      <InfoSidebar
        isOpen={ui.sidebarOpen}
        onClose={() => setUI(prev => ({ ...prev, sidebarOpen: false }))}
        stats={mockStats}
      />
      <QuickActions
        onInfoClick={handleSidebarToggle}
        onDownloadClick={handleDownloadClick}
        onAddClick={handleAddClick}
      />
      <KeyboardShortcutsModal
        isOpen={ui.shortcutsModalOpen}
        onClose={() => setUI(prev => ({ ...prev, shortcutsModalOpen: false }))}
      />
      {/* Ingest Confirm Dialog */}
      <ConfirmDialog
        isOpen={showIngestConfirm}
        onClose={() => setShowIngestConfirm(false)}
        onConfirm={handleConfirmIngest}
        title="Confirm Data Ingestion"
        message={`Queue ${bulkActions.selectedItems.length} selected document(s) for ingestion? Only records with a usable Date Published to Portal (TRC) will be queued; others will be skipped and summarized afterward.`}
        confirmText="Queue for Ingestion"
        cancelText="Cancel"
        variant="info"
        isLoading={isIngesting}
      />

      {/* Ingest by Query Confirm Dialog */}
      <ConfirmDialog
        isOpen={showIngestByQueryConfirm}
        onClose={() => setShowIngestByQueryConfirm(false)}
        onConfirm={handleConfirmIngestByQuery}
        title="Confirm Bulk Ingestion"
        message="Queue all documents matching the current filters for ingestion? Only records with a non-empty Date Published to Portal (TRC) will be included. This may affect a large number of documents."
        confirmText="Queue All Matching"
        cancelText="Cancel"
        variant="warning"
        isLoading={isIngestingByQuery}
      />

      <MissingTrcExcludedDialog
        isOpen={missingTrcDialog !== null}
        onClose={() => setMissingTrcDialog(null)}
        total={missingTrcDialog?.total ?? 0}
        recordIds={missingTrcDialog?.ids ?? []}
        idsTruncated={missingTrcDialog?.truncated ?? false}
      />

      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={handleToastClose}
          />
        </div>
      )}
      {/* <Footer /> */}
    </div>
  )
}
