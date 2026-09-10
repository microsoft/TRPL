'use client'

import React, { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate, useParams } from 'react-router-dom'
import { Search, ChevronDown, Upload, FileText, Image, Newspaper, File, Mail, Mic, BookOpen, FileCheck, ArrowUpDown, ArrowDown, Info, CheckCircle, Clock, X, CheckCircle2, AlertCircle, Loader2, Eye, ChevronRight, TrendingUp, XCircle, Undo2 } from 'lucide-react'
import { apiService, ApiCollectionDetails, ApiCreatorEntry, ApiRecipientEntry } from '@/services/api'
import { mapApiDocumentsToRecords } from '@/utils/mapApiToRecord'
import { ArchivalRecord, PaginationState } from '@/types'
import { hasPortalPublishDateMetadata } from '@/utils/portalPublishDate'
import Toast from '@/components/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'
import MissingTrcExcludedDialog from '@/components/MissingTrcExcludedDialog'
import Pagination from '@/components/Pagination'
import { FiltersProvider, useFilters } from '@/contexts/FiltersContext'
import { useAuth } from '@/contexts/AuthContext'

// Check if a record can be selected
const canSelectRecord = (record: ArchivalRecord): boolean => {

  if (record.assetCount === 0 || !record.assetCount) return false
  
  if (record.original_file_status !== 'completed') return false

  if (record.ocr_processing_status !== 'completed') return false

  return true
}

// Get the reason why a record cannot be selected
const getSelectionReason = (record: ArchivalRecord): string => {
  if (record.assetCount === 0 || !record.assetCount) return 'no assets available'
  if (record.original_file_status !== 'completed') return 'original file processing is not completed'
  if (record.ocr_processing_status !== 'completed') return 'OCR processing is not completed'
  return 'asset processing is incomplete'
}

const CollectionsItemsPageContent: React.FC = () => {
  const navigate = useNavigate()
  const { permissions } = useAuth()
  const canEdit = permissions.documents.canEdit
  const { repository, collectionName } = useParams<{ repository: string; collectionName: string }>()
  const decodedRepository = repository ? decodeURIComponent(repository) : ''
  const decodedCollectionName = collectionName ? decodeURIComponent(collectionName) : ''
  
  // Use filters from context
  const {
    searchQuery,
    typeFilter,
    sourceFilter,
    creatorFilter,
    recipientFilter,
    confidenceFilter,
    statusFilter,
    sortBy,
    setSearchQuery,
    setTypeFilter,
    setSourceFilter,
    setCreatorFilter,
    setRecipientFilter,
    setConfidenceFilter,
    setStatusFilter,
    setRepository: setContextRepository,
    setCollectionName: setContextCollectionName,
    setSortBy
  } = useFilters()
  
  // Update context when URL params change
  useEffect(() => {
    setContextRepository(decodedRepository)
    setContextCollectionName(decodedCollectionName)
  }, [decodedRepository, decodedCollectionName, setContextRepository, setContextCollectionName])
  
  const [selectedRecords, setSelectedRecords] = useState<Set<string>>(new Set())
  const [records, setRecords] = useState<ArchivalRecord[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  const [missingTrcDialog, setMissingTrcDialog] = useState<{
    total: number
    ids: string[]
    truncated: boolean
  } | null>(null)
  const [isIngesting, setIsIngesting] = useState(false)
  const [isIngestingByQuery, setIsIngestingByQuery] = useState(false)
  const [showIngestDialog, setShowIngestDialog] = useState(false)
  const [showIngestByFilterDialog, setShowIngestByFilterDialog] = useState(false)
  const [showBulkUnpublishModal, setShowBulkUnpublishModal] = useState(false)
  const [bulkUnpublishResourceType, setBulkUnpublishResourceType] = useState('')
  const [bulkUnpublishConfidence, setBulkUnpublishConfidence] = useState('')
  const [bulkUnpublishTitle, setBulkUnpublishTitle] = useState('')
  const [isBulkUnpublishing, setIsBulkUnpublishing] = useState(false)
  const [showOcrWarningDialog, setShowOcrWarningDialog] = useState(false)
  const [pendingNavigationRecord, setPendingNavigationRecord] = useState<ArchivalRecord | null>(null)
  const [collectionStats, setCollectionStats] = useState<ApiCollectionDetails | null>(null)
  const [isLoadingStats, setIsLoadingStats] = useState(false)
  const [openPopoverId, setOpenPopoverId] = useState<string | null>(null)
  const [popoverPosition, setPopoverPosition] = useState<{ top: number; left: number } | null>(null)
  const popoverButtonRef = useRef<{ [key: string]: HTMLButtonElement | null }>({})
  const [openStatusPopoverId, setOpenStatusPopoverId] = useState<string | null>(null)
  const [statusPopoverPosition, setStatusPopoverPosition] = useState<{ top: number; left: number } | null>(null)
  const statusPopoverButtonRef = useRef<{ [key: string]: HTMLElement | null }>({})
  const [showAllRecords, setShowAllRecords] = useState<boolean>(false)
  
  // Field mapping for dynamic identifier display
  const [fieldMapping, setFieldMapping] = useState<{
    identifier_field: string;
    display_name?: string;
  } | null>(null)
  
  // Pagination
  const [pagination, setPagination] = useState<PaginationState>({
    currentPage: 1,
    itemsPerPage: 20,
    totalItems: 0,
    totalPages: 0
  })
  const [isPaginating, setIsPaginating] = useState(false)

  // Get unique resource types for filter
  const [resourceTypes, setResourceTypes] = useState<string[]>([])
  useEffect(() => {
    const fetchResourceTypes = async () => {
      try {
        const response = await apiService.getResourceTypes()
        setResourceTypes(response.resource_types || [])
      } catch (error) {
        console.error('Failed to fetch resource types:', error)
      }
    }
    fetchResourceTypes()
  }, [])

  // Get unique sources for filter
  const [sources, setSources] = useState<string[]>([])
  useEffect(() => {
    const fetchSources = async () => {
      try {
        const response = await apiService.getSources()
        setSources(response.sources || [])
      } catch (error) {
        console.error('Failed to fetch sources:', error)
      }
    }
    fetchSources()
  }, [])

  // Get creators for filter (scoped to current repository/collection)
  const [creators, setCreators] = useState<ApiCreatorEntry[]>([])
  const [creatorSearchText, setCreatorSearchText] = useState('')
  const [isCreatorDropdownOpen, setIsCreatorDropdownOpen] = useState(false)
  const creatorDropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const fetchCreators = async () => {
      try {
        const response = await apiService.getCreators(decodedRepository || undefined, decodedCollectionName || undefined)
        setCreators(response.creators || [])
      } catch (error) {
        console.error('Failed to fetch creators:', error)
      }
    }
    fetchCreators()
  }, [decodedRepository, decodedCollectionName])

  // Close creator/recipient dropdowns on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node
      if (creatorDropdownRef.current && !creatorDropdownRef.current.contains(target)) {
        setIsCreatorDropdownOpen(false)
      }
      if (recipientDropdownRef.current && !recipientDropdownRef.current.contains(target)) {
        setIsRecipientDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Unique creator names, filtered by search text
  const filteredCreatorNames = Array.from(
    new Set(
      creators
        .map((c) => c.creator)
        .filter((name) => name.toLowerCase().includes(creatorSearchText.toLowerCase()))
    )
  ).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))

  // Get recipients for filter (scoped to current repository/collection)
  const [recipients, setRecipients] = useState<ApiRecipientEntry[]>([])
  const [recipientSearchText, setRecipientSearchText] = useState('')
  const [isRecipientDropdownOpen, setIsRecipientDropdownOpen] = useState(false)
  const recipientDropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const fetchRecipients = async () => {
      try {
        const response = await apiService.getRecipients(decodedRepository || undefined, decodedCollectionName || undefined)
        setRecipients(response.recipients || [])
      } catch (error) {
        console.error('Failed to fetch recipients:', error)
      }
    }
    fetchRecipients()
  }, [decodedRepository, decodedCollectionName])

  const filteredRecipientNames = Array.from(
    new Set(
      recipients
        .map((r) => r.recipient)
        .filter((name) => name.toLowerCase().includes(recipientSearchText.toLowerCase()))
    )
  ).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))

  // Fetch collection statistics
  useEffect(() => {
    const fetchCollectionStats = async () => {
      if (!decodedRepository || !decodedCollectionName) return
      
      try {
        setIsLoadingStats(true)
        const response = await apiService.getCollectionDetails(decodedRepository, decodedCollectionName)
        setCollectionStats(response.collection)
      } catch (error) {
        console.error('Failed to fetch collection statistics:', error)
      } finally {
        setIsLoadingStats(false)
      }
    }
    fetchCollectionStats()
  }, [decodedRepository, decodedCollectionName])

  // Fetch field mapping for dynamic identifier display
  useEffect(() => {
    const fetchFieldMapping = async () => {
      if (!decodedRepository) {
        setFieldMapping(null)
        return
      }
      
      try {
        const response = await apiService.resolveFieldMapping(decodedRepository, decodedCollectionName)
        setFieldMapping(response.mapping)
      } catch (error) {
        console.error('Failed to fetch field mapping:', error)
        // Default to Source Record ID if fetch fails
        setFieldMapping({ identifier_field: 'Source Record ID', display_name: 'Source Record ID' })
      }
    }
    fetchFieldMapping()
  }, [decodedRepository, decodedCollectionName])

  // Helper function to get identifier value from record based on field mapping
  const getIdentifierValue = useCallback((record: ArchivalRecord): string => {
    if (!fieldMapping) return record.documentId || '—'
    
    const fieldName = fieldMapping.identifier_field
    
    // Try to get value from rawMetadata first
    if (record.rawMetadata && record.rawMetadata[fieldName] !== undefined) {
      const value = record.rawMetadata[fieldName]
      if (typeof value === 'string') return value || '—'
      if (typeof value === 'object' && value !== null) {
        // Try common object property names
        if (value.label) return value.label
        if (value.name) return value.name
        if (value.value) return value.value
        return JSON.stringify(value)
      }
      return String(value) || '—'
    }
    
    // Fallback to documentId (which is the default Source Record ID )
    if (fieldName === 'Source Record ID') {
      return record.documentId || '—'
    }
    
    return '—'
  }, [fieldMapping])

  // Build API params
  const buildApiParams = useCallback((page: number) => {
    const params: any = {
      page_number: page,
      page_size: pagination.itemsPerPage,
      order_by: sortBy === 'Newest first' ? 'created_at' : sortBy === 'Oldest first' ? 'created_at' : 'title',
      descending: sortBy === 'Newest first' ? true : sortBy === 'Title Z-A' ? true : false,
    }

    if (searchQuery) {
      params.title_contains = searchQuery
    }

    if (statusFilter) {
      params.status = statusFilter
    }

    if (typeFilter) {
      params.resource_type = typeFilter
    }

    if (sourceFilter) {
      params.source = sourceFilter
    }

    if (creatorFilter) {
      params.creator = creatorFilter
    }

    if (recipientFilter) {
      params.recipient = recipientFilter
    }

    if (decodedRepository) {
      params.repository = decodedRepository
    }
    if (decodedCollectionName) {
      params.collection = decodedCollectionName
    }

    // Map confidence filter to numeric ranges (matching Home.tsx)
    if (confidenceFilter) {
      switch (confidenceFilter) {
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

    // Failed records can be errors or have zero assets by definition.
    if (!showAllRecords && statusFilter !== 'failed') {
      params.exclude_errors = true
      params.exclude_zero_assets = true
    }

    return params
  }, [searchQuery, typeFilter, sourceFilter, creatorFilter, recipientFilter, confidenceFilter, statusFilter, pagination.itemsPerPage, sortBy, decodedRepository, decodedCollectionName, showAllRecords])

  // Fetch records for a specific page
  const fetchRecordsPage = useCallback(async (page: number, itemsPerPageOverride?: number) => {
    setIsLoading(true)
    try {
      const itemsPerPage = itemsPerPageOverride || pagination.itemsPerPage
      const params = buildApiParams(page)
      // Override page_size if itemsPerPageOverride is provided
      if (itemsPerPageOverride) {
        params.page_size = itemsPerPageOverride
      }
      console.log('Fetching records with params:', params)
      const response = await apiService.getDocuments(params)
      console.log('API response:', response)
      const mappedRecords = mapApiDocumentsToRecords(response.documents)
      console.log('Mapped records:', mappedRecords, 'count:', mappedRecords.length)
      
      setRecords(mappedRecords)
      const totalCount = response.count || 0
      const totalPages = Math.ceil(totalCount / itemsPerPage)
      
      setPagination(prev => ({
        ...prev,
        currentPage: page,
        itemsPerPage: itemsPerPage,
        totalItems: totalCount,
        totalPages: totalPages
      }))
    } catch (error) {
      console.error('Failed to fetch records:', error)
      setToast({
        type: 'error',
        message: 'Failed to load records. Please try again.'
      })
    } finally {
      setIsLoading(false)
    }
  }, [buildApiParams, pagination.itemsPerPage])

  // Clean up selected records that are no longer selectable
  useEffect(() => {
    if (records.length > 0 && selectedRecords.size > 0) {
      setSelectedRecords(prev => {
        const newSet = new Set<string>()
        prev.forEach(recordId => {
          const record = records.find(r => r.id === recordId)
          if (record && canSelectRecord(record)) {
            newSet.add(recordId)
          }
        })
        return newSet
      })
    }
  }, [records])

  // Initial load and reset on filter change
  const isInitialMount = useRef(true)
  const lastFiltersRef = useRef<string>('')
  
  useEffect(() => {
    const currentFilters = JSON.stringify({ searchQuery, typeFilter, sourceFilter, creatorFilter, recipientFilter, confidenceFilter, statusFilter, sortBy, decodedRepository, decodedCollectionName, showAllRecords })
    
    // Skip if filters haven't changed (except on initial mount)
    if (!isInitialMount.current && currentFilters === lastFiltersRef.current) {
      console.log('Filters unchanged, skipping fetch')
      return
    }
    
    console.log('Initial load effect triggered', { searchQuery, typeFilter, sourceFilter, creatorFilter, recipientFilter, confidenceFilter, statusFilter, sortBy, decodedRepository, decodedCollectionName, showAllRecords })
    lastFiltersRef.current = currentFilters
    isInitialMount.current = false
    
    // Set loading state immediately
    setIsLoading(true)
    
    // Reset state
    setRecords([])
    setPagination(prev => ({ ...prev, currentPage: 1, totalItems: 0, totalPages: 0 }))
    
    // Fetch page 1
    fetchRecordsPage(1)
  }, [searchQuery, typeFilter, sourceFilter, creatorFilter, recipientFilter, confidenceFilter, statusFilter, sortBy, decodedRepository, decodedCollectionName, showAllRecords, fetchRecordsPage])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setRecords([])
    setPagination(prev => ({ ...prev, currentPage: 1, totalItems: 0, totalPages: 0 }))
    fetchRecordsPage(1)
  }

  // Handle page change
  const handlePageChange = useCallback(
    async (targetPage: number) => {
      if (isPaginating || targetPage === pagination.currentPage) return

      setIsPaginating(true)
      try {
        await fetchRecordsPage(targetPage)
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
    [isPaginating, pagination.currentPage, fetchRecordsPage]
  )

  // Handle items per page change
  const handleItemsPerPageChange = useCallback(
    async (newItemsPerPage: number) => {
      await fetchRecordsPage(1, newItemsPerPage)
    },
    [fetchRecordsPage]
  )

  const handleRecordClick = (record: ArchivalRecord) => {
    // Check if record has any assets
    if (record.assetCount === 0) {
      setToast({
        type: 'error',
        message: 'No assets available for this record. Cannot open for review.'
      })
      return
    }

    // Check if original asset is available
    if (record.original_file_status !== 'completed') {
      setToast({
        type: 'error',
        message: 'Original asset not available. Cannot open this record for review.'
      })
      return
    }

    // Check if OCR is completed
    if (record.ocr_processing_status !== 'completed') {
      setPendingNavigationRecord(record)
      setShowOcrWarningDialog(true)
      return
    }

    // Navigate to review page with page info in URL for faster navigation
    // Pass page number and index so Review page knows which page to load
    const recordIndex = records.findIndex(r => r.id === record.id)
    const pageIndex = recordIndex >= 0 ? recordIndex : 0
    const url = `/repositories/${encodeURIComponent(decodedRepository)}/collections/${encodeURIComponent(decodedCollectionName)}/review/${record.id}?page=${pagination.currentPage}&index=${pageIndex}`
    navigate(url)
  }

  const handleOcrWarningConfirm = useCallback(() => {
    if (pendingNavigationRecord) {
      navigate(`/repositories/${encodeURIComponent(decodedRepository)}/collections/${encodeURIComponent(decodedCollectionName)}/review/${pendingNavigationRecord.id}`)
    }
    setShowOcrWarningDialog(false)
    setPendingNavigationRecord(null)
  }, [pendingNavigationRecord, decodedRepository, decodedCollectionName, navigate])

  const handleOcrWarningCancel = useCallback(() => {
    setShowOcrWarningDialog(false)
    setPendingNavigationRecord(null)
  }, [])

  const handleRecordSelect = (recordId: string, e: React.MouseEvent | React.ChangeEvent<HTMLInputElement>) => {
    e.stopPropagation()
    
    // Find the record to check if it can be selected
    const record = records.find(r => r.id === recordId)
    if (record && !canSelectRecord(record)) {
      // Show a toast message explaining why selection is not allowed
      const reason = getSelectionReason(record)
      setToast({
        type: 'error',
        message: `Cannot select this record: ${reason}`
      })
      return
    }
    
    setSelectedRecords(prev => {
      const newSet = new Set(prev)
      if (newSet.has(recordId)) {
        newSet.delete(recordId)
      } else {
        newSet.add(recordId)
      }
      return newSet
    })
  }

  const handleIngestClick = useCallback(() => {
    if (selectedRecords.size === 0) {
      setToast({
        type: 'error',
        message: 'Please select at least one document to ingest'
      })
      return
    }
    setShowIngestDialog(true)
  }, [selectedRecords.size])

  const handleIngestConfirm = useCallback(async () => {
    if (selectedRecords.size === 0) return

    const recordIds = Array.from(selectedRecords)
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
        message:
          'Cannot publish: none of the selected records have a usable Date Published to Portal value. Add one, then try again.',
      })
      setShowIngestDialog(false)
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
        message:
          result.message ||
          (lackingTrc.length > 0
            ? `Queued ${withTrc.length} document(s) with TRC. ${lackingTrc.length} selected without TRC were skipped.`
            : `Successfully queued ${withTrc.length} document(s) for ingestion`),
      })

      // Clear selection after successful ingest
      setSelectedRecords(new Set())
    } catch (error) {
      console.error('Failed to ingest documents:', error)
      setToast({
        type: 'error',
        message: error instanceof Error ? error.message : 'Failed to queue documents for ingestion'
      })
    } finally {
      setIsIngesting(false)
      setShowIngestDialog(false)
    }
  }, [selectedRecords, records])

  const handleIngestByFilterClick = useCallback(() => {
    // Build query parameters from current filters
    const queryParams: {
      status?: string;
      repository?: string;
      collection?: string;
      resource_type?: string;
      min_confidence?: number;
      max_confidence?: number;
      title_contains?: string;
    } = {}

    if (statusFilter) {
      queryParams.status = statusFilter
    }

    if (typeFilter) {
      queryParams.resource_type = typeFilter
    }

    if (decodedRepository) {
      queryParams.repository = decodedRepository
    }
    if (decodedCollectionName) {
      queryParams.collection = decodedCollectionName
    }

    if (searchQuery) {
      queryParams.title_contains = searchQuery
    }

    // Map confidence filter to numeric ranges (matching Home.tsx)
    if (confidenceFilter) {
      switch (confidenceFilter) {
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
        default:
          break
      }
    }

    // Require status filter for bulk operations. Other filters are optional.
    if (!statusFilter) {
      setToast({
        type: 'error',
        message: 'Please select a status and any additional filters before publishing records'
      })
      return
    }

    setShowIngestByFilterDialog(true)
  }, [statusFilter, typeFilter, decodedRepository, decodedCollectionName, searchQuery, confidenceFilter])

  const handleIngestByFilterConfirm = useCallback(async () => {
    // Build query parameters from current filters
    const queryParams: {
      status?: string;
      repository?: string;
      collection?: string;
      resource_type?: string;
      min_confidence?: number;
      max_confidence?: number;
      title_contains?: string;
    } = {}

    if (statusFilter) {
      queryParams.status = statusFilter
    }

    if (typeFilter) {
      queryParams.resource_type = typeFilter
    }

    if (decodedRepository) {
      queryParams.repository = decodedRepository
    }
    if (decodedCollectionName) {
      queryParams.collection = decodedCollectionName
    }

    if (searchQuery) {
      queryParams.title_contains = searchQuery
    }

    // Map confidence filter to numeric ranges (matching Home.tsx)
    if (confidenceFilter) {
      switch (confidenceFilter) {
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
        default:
          break
      }
    }

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
        message: result.message || 'Successfully queued documents matching the filters for ingestion'
      })
    } catch (error) {
      console.error('Failed to ingest documents by query:', error)
      setToast({
        type: 'error',
        message: error instanceof Error ? error.message : 'Failed to queue documents for ingestion'
      })
    } finally {
      setIsIngestingByQuery(false)
      setShowIngestByFilterDialog(false)
    }
  }, [statusFilter, typeFilter, decodedRepository, decodedCollectionName, searchQuery, confidenceFilter])

  const openBulkUnpublishModal = useCallback(() => {
    setBulkUnpublishResourceType(typeFilter)
    setBulkUnpublishConfidence(confidenceFilter)
    setBulkUnpublishTitle(searchQuery)
    setShowBulkUnpublishModal(true)
  }, [typeFilter, confidenceFilter, searchQuery])

  const handleBulkUnpublishConfirm = useCallback(async () => {
    if (!decodedRepository || !decodedCollectionName) {
      setToast({ type: 'error', message: 'Repository or collection is missing.' })
      return
    }

    const params: {
      repository: string
      collection: string
      resource_type?: string
      min_confidence?: number
      max_confidence?: number
      title_contains?: string
    } = {
      repository: decodedRepository,
      collection: decodedCollectionName,
    }

    if (bulkUnpublishResourceType) {
      params.resource_type = bulkUnpublishResourceType
    }

    if (bulkUnpublishTitle.trim()) {
      params.title_contains = bulkUnpublishTitle.trim()
    }

    if (bulkUnpublishConfidence) {
      switch (bulkUnpublishConfidence) {
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
          break
      }
    }

    try {
      setIsBulkUnpublishing(true)
      const result = await apiService.unpublishDocumentsByFilters(params)
      setToast({
        type: 'success',
        message: result.message || 'Unpublish job started',
      })
      setShowBulkUnpublishModal(false)
    } catch (error) {
      console.error('Failed to start bulk unpublish:', error)
      setToast({
        type: 'error',
        message: error instanceof Error ? error.message : 'Failed to start bulk unpublish',
      })
    } finally {
      setIsBulkUnpublishing(false)
    }
  }, [
    decodedRepository,
    decodedCollectionName,
    bulkUnpublishResourceType,
    bulkUnpublishConfidence,
    bulkUnpublishTitle,
  ])


  const formatDate = (dateString: string) => {
    if (!dateString) return '—'
    try {
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return '—'
      const year = date.getFullYear()
      const month = String(date.getMonth() + 1).padStart(2, '0')
      const day = String(date.getDate()).padStart(2, '0')
      return `${year}-${month}-${day}`
    } catch {
      return '—'
    }
  }

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 90) return 'text-green-600'
    if (confidence >= 70) return 'text-yellow-600'
    if (confidence >= 50) return 'text-orange-600'
    return 'text-red-600'
  }

  const getConfidenceBarColor = (confidence: number) => {
    if (confidence >= 90) return 'bg-green-500'
    if (confidence >= 70) return 'bg-yellow-500'
    if (confidence >= 50) return 'bg-orange-500'
    return 'bg-red-500'
  }

  const getStatusIcon = (status?: string) => {
    if (!status) return null
    
    const statusLower = status.toLowerCase()
    if (statusLower === 'completed' || statusLower === 'success') {
      return <CheckCircle2 className="w-4 h-4 text-green-600" />
    } else if (statusLower === 'error' || statusLower === 'failed') {
      return <AlertCircle className="w-4 h-4 text-red-600" />
    } else if (statusLower === 'pending' || statusLower === 'processing' || statusLower === 'in_progress') {
      return <Clock className="w-4 h-4 text-yellow-600" />
    }
    return <Clock className="w-4 h-4 text-gray-400" />
  }

  const getStatusBadge = (status: string, record: ArchivalRecord) => {
    const baseClasses = "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium"
    const isPending = status === 'pending'
    
    // Check if any pipeline stage has an error or failed status
    const pipelineStages = [
      record.related_assets_status,
      record.asset_details_status,
      record.original_file_status,
      record.ocr_batch_status,
      record.ocr_processing_status,
      record.metadata_extraction_status
    ]
    const hasError = pipelineStages.some(s => s === 'error' || s === 'failed')
    
    // If record is pending and any stage has an error, show error status
    if (isPending && hasError) {
      return (
        <div 
          className="relative inline-block"
          onMouseEnter={(e) => {
            const button = statusPopoverButtonRef.current[record.id]
            if (button) {
              const rect = button.getBoundingClientRect()
              setStatusPopoverPosition({
                top: rect.top - 8,
                left: rect.left
              })
            }
            setOpenStatusPopoverId(record.id)
          }}
          onMouseLeave={() => {
            setTimeout(() => {
              const popover = document.querySelector('.status-popover')
              if (!popover || !popover.matches(':hover')) {
                setOpenStatusPopoverId(null)
                setStatusPopoverPosition(null)
              }
            }, 150)
          }}
        >
          <span 
            ref={(el) => {
              statusPopoverButtonRef.current[record.id] = el
            }}
            className={`${baseClasses} bg-red-100 text-red-800 cursor-pointer`}
          >
            <AlertCircle className="w-3 h-3 mr-1" />
            Error
          </span>
        </div>
      )
    }
    
    if (status === 'failed') {
      return (
        <span className={`${baseClasses} bg-red-100 text-red-800`}>
          <AlertCircle className="w-3 h-3 mr-1" />
          Failed
        </span>
      )
    } else if (status === 'published') {
      return (
        <span className={`${baseClasses} bg-green-100 text-green-800`}>
          <CheckCircle className="w-3 h-3 mr-1" />
          Published
        </span>
      )
    } else if (status === 'publishing') {
      return (
        <span className={`${baseClasses} bg-purple-100 text-purple-800`}>
          <Clock className="w-3 h-3 mr-1" />
          Publishing
        </span>
      )
    } else if (status === 'reviewed') {
      return (
        <span className={`${baseClasses} bg-blue-100 text-blue-800`}>
          <CheckCircle className="w-3 h-3 mr-1" />
          Reviewed
        </span>
      )
    } else if (isPending) {
      return (
        <div 
          className="relative inline-block"
          onMouseEnter={(e) => {
            const button = statusPopoverButtonRef.current[record.id]
            if (button) {
              const rect = button.getBoundingClientRect()
              setStatusPopoverPosition({
                top: rect.top - 8,
                left: rect.left
              })
            }
            setOpenStatusPopoverId(record.id)
          }}
          onMouseLeave={() => {
            setTimeout(() => {
              const popover = document.querySelector('.status-popover')
              if (!popover || !popover.matches(':hover')) {
                setOpenStatusPopoverId(null)
                setStatusPopoverPosition(null)
              }
            }, 150)
          }}
        >
          <span 
            ref={(el) => {
              statusPopoverButtonRef.current[record.id] = el
            }}
            className={`${baseClasses} bg-yellow-100 text-yellow-800 cursor-pointer`}
          >
            <Clock className="w-3 h-3 mr-1" />
            Pending
          </span>
        </div>
      )
    }
    return (
      <span className={`${baseClasses} bg-gray-100 text-gray-800`}>
        Draft
      </span>
    )
  }

  const getResourceIcon = (resourceType: string) => {
    const type = (resourceType || '').toLowerCase()
    
    // Correspondence / Letters
    if (type.includes('letter') || type.includes('correspondence') || type.includes('mail')) {
      return <Mail className="w-5 h-5 text-gray-600" />
    }
    
    // Photographs / Images
    if (type.includes('photo') || type.includes('photograph') || type.includes('image') || type.includes('picture')) {
      return <Image className="w-5 h-5 text-gray-600" />
    }
    
    // News Articles
    if (type.includes('article') || type.includes('news') || type.includes('newspaper')) {
      return <Newspaper className="w-5 h-5 text-gray-600" />
    }
    
    // Speeches
    if (type.includes('speech') || type.includes('address') || type.includes('lecture')) {
      return <Mic className="w-5 h-5 text-gray-600" />
    }
    
    // Diaries / Journals
    if (type.includes('diary') || type.includes('journal') || type.includes('log')) {
      return <BookOpen className="w-5 h-5 text-gray-600" />
    }
    
    // Official Documents
    if (type.includes('official') || type.includes('document') || type.includes('record')) {
      return <FileCheck className="w-5 h-5 text-gray-600" />
    }
    
    // Default fallback
    return <FileText className="w-5 h-5 text-gray-600" />
  }

  const getResourceTypeBadge = (resourceType: string, assetCount?: number) => {
    if (!resourceType) {
      return <span className="px-2 py-1 rounded text-xs font-medium bg-gray-100 text-gray-800">Unknown</span>
    }
    // Format resource type: replace underscores with spaces and capitalize
    const formatted = resourceType.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
    const countText = assetCount !== undefined ? ` (${assetCount})` : ''
    return <span className="px-2 py-1 rounded text-xs font-medium bg-purple-100 text-purple-800">{formatted}{countText}</span>
  }


  return (
    <div className="bg-gray-50">
      {/* Title and Description Section (below breadcrumb) */}
      <section className="bg-museum-green border-b border-gray-200 px-6 py-6 shadow-sm">
        <div className="max-w-8xl mx-auto">
          <div className="text-left">
            <h1 className="text-2xl font-bold text-white">Review Queue</h1>
            <p className="mt-1 text-sm text-white/80">
              Items requiring archivist validation and correction
            </p>
          </div>
        </div>
      </section>

      {/* Collection Statistics - Single Row */}
      <div className="px-6 py-3 bg-white border-b border-gray-200">
        <div className="max-w-8xl mx-auto">
          <div className="flex items-center gap-4">
            {/* Total Items */}
            <div className="flex items-center gap-2 pr-5 border-r border-gray-200">
              <div className="w-8 h-8 bg-slate-100 rounded-lg flex items-center justify-center">
                <FileText className="w-4 h-4 text-slate-600" />
              </div>
              <div>
                <p className="text-xs text-gray-500">Total Items</p>
                <p className="text-base font-bold text-gray-900">
                  {isLoadingStats ? '...' : (collectionStats?.totalItems || pagination.totalItems || 0).toLocaleString()}
                </p>
              </div>
            </div>

            {/* Pipeline Stats */}
            <div className="flex items-center gap-3 flex-1">
              {(() => {
                const totalItems = collectionStats?.totalItems || 0
                const stages = [
                  {
                    label: 'Pending',
                    count: collectionStats?.pending || 0,
                    icon: Clock,
                    bgColor: 'bg-amber-500',
                    lightBg: 'bg-amber-50',
                    textColor: 'text-amber-700',
                    borderColor: 'border-amber-200'
                  },
                  {
                    label: 'Reviewed',
                    count: collectionStats?.reviewed || 0,
                    icon: Eye,
                    bgColor: 'bg-blue-500',
                    lightBg: 'bg-blue-50',
                    textColor: 'text-blue-700',
                    borderColor: 'border-blue-200'
                  },
                  {
                    label: 'Publishing',
                    count: collectionStats?.publishing || 0,
                    icon: Loader2,
                    bgColor: 'bg-purple-500',
                    lightBg: 'bg-purple-50',
                    textColor: 'text-purple-700',
                    borderColor: 'border-purple-200'
                  },
                  {
                    label: 'Published',
                    count: collectionStats?.published || 0,
                    icon: CheckCircle,
                    bgColor: 'bg-green-500',
                    lightBg: 'bg-green-50',
                    textColor: 'text-green-700',
                    borderColor: 'border-green-200'
                  },
                  {
                    label: 'Errors',
                    count: collectionStats?.errors || 0,
                    icon: XCircle,
                    bgColor: 'bg-red-500',
                    lightBg: 'bg-red-50',
                    textColor: 'text-red-700',
                    borderColor: 'border-red-200'
                  }
                ]
                
                return stages.map((stage, index) => {
                  const Icon = stage.icon
                  const percentage = totalItems > 0 
                    ? Math.round((stage.count / totalItems) * 100) 
                    : 0
                  
                  return (
                    <React.Fragment key={stage.label}>
                      <div className={`flex-1 flex items-center gap-2 px-3 py-2 ${stage.lightBg} rounded-lg border ${stage.borderColor}`}>
                        <div className={`w-7 h-7 ${stage.bgColor} rounded flex items-center justify-center flex-shrink-0`}>
                          <Icon className={`w-3.5 h-3.5 text-white ${stage.label === 'Publishing' && stage.count > 0 ? 'animate-spin' : ''}`} />
                        </div>
                        <div className="text-left">
                          <p className="text-base font-bold text-gray-900 leading-tight">
                            {isLoadingStats ? '...' : stage.count.toLocaleString()}
                          </p>
                          <p className={`text-xs ${stage.textColor}`}>
                            {stage.label} {!isLoadingStats && `(${percentage}%)`}
                          </p>
                        </div>
                      </div>
                      {index < stages.length - 1 && (
                        <ChevronRight className="w-4 h-4 text-gray-300 flex-shrink-0" />
                      )}
                    </React.Fragment>
                  )
                })
              })()}
            </div>

            {/* Completion Rate */}
            <div className="flex items-center gap-2 pl-5 border-l border-gray-200">
              <TrendingUp className="w-5 h-5 text-green-600" />
              <div>
                <p className="text-base font-bold text-gray-900">
                  {isLoadingStats ? '...' : `${(collectionStats?.completionRate || 0).toFixed(1)}%`}
                </p>
                <p className="text-xs text-gray-500">Complete</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 relative" style={{ zIndex: 10 }}>
        <div className="max-w-8xl mx-auto">
          {/* Search, Filters, Pagination and Sort Controls - All in one line */}
          <div className="flex items-center justify-between flex-wrap gap-4">
            {/* Search Bar */}
            <form onSubmit={handleSearch} className="flex items-center gap-3 mt-5">
              <div className="relative">
                <Search className="absolute right-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-5 h-5 pointer-events-none" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => {
                    setSearchQuery(e.target.value)
                  }}
                  placeholder="Search by title and date (YYYY-MM-DD)..."
                  className="pl-4 pr-10 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 w-96"
                />
              </div>
            </form>

            {/* Filters */}
            <div className="flex items-end space-x-4 flex-1">
              <div>
                <label
                  htmlFor="resource-type-filter"
                  className="block text-sm text-gray-700 mb-1 font-semibold text-left"
                >
                  Resource Type
                </label>
                <div className="relative">
                  <select
                    id="resource-type-filter"
                    value={typeFilter}
                    onChange={(e) => {
                      setTypeFilter(e.target.value)
                    }}
                    className="appearance-none bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                  >
                    <option value="">All Types</option>
                    {resourceTypes.map((type) => (
                      <option key={type} value={type}>{type}</option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                </div>
              </div>

              <div>
                <label
                  htmlFor="source-filter"
                  className="block text-sm text-gray-700 mb-1 font-semibold text-left"
                >
                  Source
                </label>
                <div className="relative">
                  <select
                    id="source-filter"
                    value={sourceFilter}
                    onChange={(e) => {
                      setSourceFilter(e.target.value)
                    }}
                    className="appearance-none bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                  >
                    <option value="">All Sources</option>
                    {sources.map((src) => (
                      <option key={src} value={src}>{src}</option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                </div>
              </div>

              <div ref={creatorDropdownRef} className="relative">
                <label
                  htmlFor="creator-filter"
                  className="block text-sm text-gray-700 mb-1 font-semibold text-left"
                >
                  Creator
                </label>
                <div className="relative">
                  <input
                    id="creator-filter"
                    type="text"
                    value={isCreatorDropdownOpen ? creatorSearchText : (creatorFilter || '')}
                    onChange={(e) => {
                      setCreatorSearchText(e.target.value)
                      if (!isCreatorDropdownOpen) setIsCreatorDropdownOpen(true)
                    }}
                    onFocus={() => {
                      setIsCreatorDropdownOpen(true)
                      setCreatorSearchText('')
                    }}
                    placeholder={creatorFilter || 'All Creators'}
                    className="appearance-none bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 w-48"
                    autoComplete="off"
                  />
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                  {creatorFilter && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        setCreatorFilter('')
                        setCreatorSearchText('')
                        setIsCreatorDropdownOpen(false)
                      }}
                      className="absolute right-7 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </div>
                {isCreatorDropdownOpen && (
                  <div className="absolute z-50 mt-1 w-72 max-h-64 overflow-y-auto bg-white border border-gray-300 rounded-lg shadow-lg">
                    <button
                      type="button"
                      onClick={() => {
                        setCreatorFilter('')
                        setCreatorSearchText('')
                        setIsCreatorDropdownOpen(false)
                      }}
                      className="w-full text-left px-4 py-2 text-sm text-gray-500 hover:bg-gray-100"
                    >
                      All Creators
                    </button>
                    {filteredCreatorNames.length === 0 && creatorSearchText && (
                      <div className="px-4 py-3 text-sm text-gray-400 italic">No results</div>
                    )}
                    {filteredCreatorNames.map((name) => (
                      <button
                        key={name}
                        type="button"
                        onClick={() => {
                          setCreatorFilter(name)
                          setCreatorSearchText('')
                          setIsCreatorDropdownOpen(false)
                        }}
                        className={`w-full text-left px-4 py-1.5 text-sm hover:bg-blue-50 ${
                          creatorFilter === name ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-700'
                        }`}
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div ref={recipientDropdownRef} className="relative">
                <label
                  htmlFor="recipient-filter"
                  className="block text-sm text-gray-700 mb-1 font-semibold text-left"
                >
                  Recipient
                </label>
                <div className="relative">
                  <input
                    id="recipient-filter"
                    type="text"
                    value={isRecipientDropdownOpen ? recipientSearchText : (recipientFilter || '')}
                    onChange={(e) => {
                      setRecipientSearchText(e.target.value)
                      if (!isRecipientDropdownOpen) setIsRecipientDropdownOpen(true)
                    }}
                    onFocus={() => {
                      setIsRecipientDropdownOpen(true)
                      setRecipientSearchText('')
                    }}
                    placeholder={recipientFilter || 'All Recipients'}
                    className="appearance-none bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 w-48"
                    autoComplete="off"
                  />
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                  {recipientFilter && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        setRecipientFilter('')
                        setRecipientSearchText('')
                        setIsRecipientDropdownOpen(false)
                      }}
                      className="absolute right-7 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </div>
                {isRecipientDropdownOpen && (
                  <div className="absolute z-50 mt-1 w-72 max-h-64 overflow-y-auto bg-white border border-gray-300 rounded-lg shadow-lg">
                    <button
                      type="button"
                      onClick={() => {
                        setRecipientFilter('')
                        setRecipientSearchText('')
                        setIsRecipientDropdownOpen(false)
                      }}
                      className="w-full text-left px-4 py-2 text-sm text-gray-500 hover:bg-gray-100"
                    >
                      All Recipients
                    </button>
                    {filteredRecipientNames.length === 0 && recipientSearchText && (
                      <div className="px-4 py-3 text-sm text-gray-400 italic">No results</div>
                    )}
                    {filteredRecipientNames.map((name) => (
                      <button
                        key={name}
                        type="button"
                        onClick={() => {
                          setRecipientFilter(name)
                          setRecipientSearchText('')
                          setIsRecipientDropdownOpen(false)
                        }}
                        className={`w-full text-left px-4 py-1.5 text-sm hover:bg-blue-50 ${
                          recipientFilter === name ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-700'
                        }`}
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <label
                  htmlFor="confidence-filter"
                  className="block text-sm text-gray-700 mb-1 font-semibold text-left"
                >
                  OCR Confidence
                </label>
                <div className="relative">
                  <select
                    id="confidence-filter"
                    value={confidenceFilter}
                    onChange={(e) => {
                      setConfidenceFilter(e.target.value)
                    }}
                    className="appearance-none bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                  >
                    <option value="">All Levels</option>
                    <option value="high">Very High (90–100%)</option>
                    <option value="medium">High (70–89%)</option>
                    <option value="low">Medium (50–69%)</option>
                    <option value="very-low">Low (&lt;50%)</option>
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                </div>
              </div>

              <div>
                <label
                  htmlFor="status-filter"
                  className="block text-sm text-gray-700 mb-1 font-semibold text-left"
                >
                  Status
                </label>
                <div className="relative">
                  <select
                    id="status-filter"
                    value={statusFilter}
                    onChange={(e) => {
                      setStatusFilter(e.target.value)
                    }}
                    className="appearance-none bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                  >
                    <option value="">All Statuses</option>
                    <option value="pending">Pending</option>
                    <option value="reviewed">Reviewed</option>
                    <option value="published">Published</option>
                    <option value="publishing">Publishing</option>
                    <option value="failed">Failed</option>
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                </div>
              </div>

              {/* Show All / Hide Errors Switch */}
              <div className="flex items-center">
                <label className="flex items-center gap-2 cursor-pointer select-none group relative">
                  <span className="text-sm text-gray-700 font-medium">Show All</span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={showAllRecords}
                    onClick={() => setShowAllRecords(!showAllRecords)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-museum-accent focus:ring-offset-2 ${
                      showAllRecords ? 'bg-museum-accent' : 'bg-gray-300'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                        showAllRecords ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                  <div className="relative">
                    <Info className="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help peer" />
                    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 bg-gray-800 text-white text-xs rounded-lg shadow-lg whitespace-nowrap opacity-0 invisible peer-hover:opacity-100 peer-hover:visible transition-all duration-200 z-50">
                      <div className="text-center">
                        <strong>When OFF:</strong> Hides records with errors<br />and records with no assets
                      </div>
                      <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-1">
                        <div className="border-4 border-transparent border-t-gray-800"></div>
                      </div>
                    </div>
                  </div>
                </label>
              </div>
            </div>

            {/* Sort Controls */}
            <div className="flex items-center space-x-4">
              <div className="flex items-center space-x-2">
                <span className="text-sm text-gray-600">Sort by:</span>
                <div className="relative">
                  <select
                    value={sortBy}
                    onChange={(e) => {
                      setSortBy(e.target.value)
                    }}
                    className="appearance-none bg-white border border-gray-300 rounded px-3 py-1 pr-6 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                >
                  <option>Newest first</option>
                  <option>Oldest first</option>
                  <option>Title A-Z</option>
                  <option>Title Z-A</option>
                </select>
                <ChevronDown className="absolute right-1 top-1/2 transform -translate-y-1/2 text-gray-400 w-3 h-3 pointer-events-none" />
              </div>
              </div>
            </div>

            {/* Ingest Buttons */}
            {canEdit && (
            <div className="flex items-center">
              {selectedRecords.size > 0 ? (
                <button
                  onClick={handleIngestClick}
                  disabled={isIngesting}
                  className="px-4 py-2 bg-museum-accent text-white rounded-lg hover:bg-museum-accent/90 focus:outline-none focus:ring-2 focus:ring-museum-accent focus:ring-offset-2 transition-colors font-medium flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isIngesting ? (
                    <>
                      <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                      <span>Indexing...</span>
                    </>
                  ) : (
                    <>
                      <Upload className="w-4 h-4" />
                      <span>Publish the Selected Records ({selectedRecords.size})</span>
                    </>
                  )}
                </button>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={handleIngestByFilterClick}
                    disabled={isIngestingByQuery || pagination.totalItems === 0}
                    className="px-4 py-2 bg-museum-accent text-white rounded-lg hover:bg-museum-accent/90 focus:outline-none focus:ring-2 focus:ring-museum-accent focus:ring-offset-2 transition-colors font-medium flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isIngestingByQuery ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                        <span>Indexing...</span>
                      </>
                    ) : (
                      <>
                        <Upload className="w-4 h-4" />
                        <span>Publish Records with Applied Filters</span>
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={openBulkUnpublishModal}
                    disabled={pagination.totalItems === 0}
                    className="px-4 py-2 bg-white text-red-700 border border-red-200 rounded-lg hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 transition-colors font-medium flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Undo2 className="w-4 h-4" />
                    <span>Bulk Unpublish</span>
                  </button>
                </div>
              )}
            </div>
            )}
          </div>
        </div>
      </div>

      {/* Table with Infinite Scroll */}
        <div className="max-w-8xl mx-auto">
          {records.length === 0 && !isLoading ? (
            <div className="text-center py-12 text-gray-500">
              No records found
            </div>
          ) : (
            <>
              <section className="bg-white relative" style={{ zIndex: 1 }}>
                {isLoading && (
                  <div className="absolute inset-0 bg-white bg-opacity-75 flex justify-center items-center z-20">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-museum-600"></div>
                    <span className="ml-4 text-museum-600 font-medium">Loading records...</span>
                  </div>
                )}
                <div className="overflow-x-auto custom-scrollbar relative" style={{ zIndex: 1 }}>
                  <table className="w-full">
                    <thead className="bg-gray-50 border-b border-gray-200">
                      <tr>
                        {canEdit && (
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900 w-12">
                        </th>
                        )}
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900 w-1/3">
                          <div className="flex items-center justify-center space-x-1">
                            <span>Title</span>
                            <ArrowUpDown className="w-3 h-3 text-gray-400" aria-hidden="true" />
                          </div>
                        </th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900 w-36">
                          <div className="flex items-center justify-center space-x-1">
                            <span>Date Created</span>
                            <ArrowDown className="w-3 h-3 text-gray-600" aria-hidden="true" />
                          </div>
                        </th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">Creator(s)</th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">Recipient</th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">
                          {fieldMapping?.display_name || fieldMapping?.identifier_field || 'Source Record ID'}
                        </th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">
                          <div className="flex items-center justify-center space-x-1">
                            <span>OCR Confidence</span>
                            <Info className="w-3 h-3 text-gray-400" aria-hidden="true" />
                          </div>
                        </th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">
                          <div className="flex items-center justify-center space-x-1">
                            <span>Metadata Confidence</span>
                            <Info className="w-3 h-3 text-gray-400" aria-hidden="true" />
                          </div>
                        </th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">Status</th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900">Resource Type</th>
                        <th className="px-6 py-4 text-center text-sm font-semibold text-gray-900 w-44">
                          Reviewer/Approver
                        </th>
                      </tr>
                    </thead>

                    <tbody className="divide-y divide-gray-200">
                      {records.map((record) => {
                        const isSelected = selectedRecords.has(record.id)
                        const isSelectable = canSelectRecord(record)
                        return (
                          <tr
                            key={record.id}
                            className="hover:bg-gray-50 transition-colors group cursor-pointer"
                            onClick={(e) => {
                              // Don't trigger row click when clicking checkbox or its container
                              const target = e.target as HTMLElement
                              const inputTarget = target as HTMLInputElement
                              if (target.closest('input[type="checkbox"]') || 
                                  target.closest('td:first-child') ||
                                  (target.tagName === 'INPUT' && inputTarget.type === 'checkbox')) {
                                return
                              }
                              handleRecordClick(record)
                            }}
                          >
                            {canEdit && (
                            <td className="px-6 py-4">
                              <div className="flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
                                {record.status !== 'published' && (
                                  <input
                                    type="checkbox"
                                    checked={isSelected}
                                    disabled={!isSelectable}
                                    onChange={(e) => {
                                      e.stopPropagation()
                                      handleRecordSelect(record.id, e)
                                    }}
                                    onClick={(e) => e.stopPropagation()}
                                    className={`w-5 h-5 bg-white border border-gray-300 rounded-full focus:ring-2 focus:ring-museum-accent appearance-none checked:bg-museum-green pointer-events-auto ${
                                      isSelectable 
                                        ? 'cursor-pointer' 
                                        : 'cursor-not-allowed opacity-50'
                                    }`}
                                    style={{
                                      backgroundImage: isSelected
                                        ? "url(\"data:image/svg+xml,%3csvg viewBox='0 0 16 16' fill='white' xmlns='http://www.w3.org/2000/svg'%3e%3cpath d='M12.207 4.793a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L4 12.586l7.793-7.793a1 1 0 011.414 0z'/%3e%3c/svg%3e\")"
                                        : "none",
                                      backgroundSize: '65%',
                                      backgroundPosition: '50% 50%',
                                      backgroundRepeat: 'no-repeat',
                                      display: 'flex',
                                      alignItems: 'center',
                                      justifyContent: 'center'
                                    }}
                                    aria-label={`Select record ${record.title}`}
                                    title={!isSelectable ? `This record cannot be selected because ${getSelectionReason(record)}.` : undefined}
                                  />
                                )}
                              </div>
                            </td>
                            )}
                            <td className="px-6 py-4 text-left group-hover:bg-gray-50">
                              <div className="flex items-center space-x-3">
                                <div className="flex-shrink-0">
                                  {getResourceIcon(record.resourceType)}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-0">
                                    <h3 className="text-sm font-semibold text-gray-900 group-hover:text-museum-accent transition-colors text-left">{record.title}</h3>
                                    {record.content && (
                                      <div 
                                        className="relative inline-block"
                                        onMouseEnter={(e) => {
                                          const button = popoverButtonRef.current[record.id]
                                          if (button) {
                                            const rect = button.getBoundingClientRect()
                                            setPopoverPosition({
                                              top: rect.top - 8,
                                              left: rect.left
                                            })
                                          }
                                          setOpenPopoverId(record.id)
                                        }}
                                        onMouseLeave={() => {
                                          // Small delay to allow moving to popover
                                          setTimeout(() => {
                                            const popover = document.querySelector('.description-popover')
                                            if (!popover || !popover.matches(':hover')) {
                                              setOpenPopoverId(null)
                                              setPopoverPosition(null)
                                            }
                                          }, 150)
                                        }}
                                      >
                                        <button
                                          ref={(el) => {
                                            popoverButtonRef.current[record.id] = el
                                          }}
                                          className="text-gray-400 hover:text-gray-600 transition-colors p-0"
                                          aria-label="Show description"
                                          onClick={(e) => e.stopPropagation()}
                                        >
                                          <Info className="w-4 h-4 ml-2" />
                                        </button>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            </td>
                            <td className="px-6 py-4 text-sm text-gray-900 w-36">
                              <div>
                                <p className="font-medium">{String(record.date)}</p>
                              </div>
                            </td>
                            <td className="px-6 py-4 text-sm">
                              <div className="space-y-1">
                                <p className="font-medium text-gray-900">{record.creator.name || 'Unknown'}</p>
                                {record.creator.role && (
                                  <p className="text-gray-500 capitalize">{record.creator.role}</p>
                                )}
                              </div>
                            </td>
                            <td className="px-6 py-4 text-sm text-gray-900">
                              <p className="font-medium">{record.recipient || 'Unknown'}</p>
                            </td>
                            <td className="px-6 py-4 text-sm text-gray-600">
                              <div>
                                <p>{getIdentifierValue(record)}</p>
                              </div>
                            </td>
                            <td className="px-6 py-4">
                              {(!record.assetCount || record.assetCount === 0) ? (
    <span className="text-sm font-medium text-museum-500">
      No Asset Available
    </span>
  ) : record.ocr_processing_status === "completed" ? (
                                  <span className="text-sm font-medium text-gray-500">
                                    {(() => {
                                      const visualDesc = record.visual_description_possible;
                                      // Check if visual description is available
                                      const isVisual = visualDesc && String(visualDesc).toUpperCase().trim() === "Y";
                                      return isVisual ? "Visual Description" : (<div className="flex items-center space-x-2">
                                    <div className="flex-1 bg-gray-200 rounded-full h-2">
                                      <div
                                        className={`h-2 rounded-full ${getConfidenceBarColor(record.ocrConfidence)}`}
                                        style={{ width: `${record.ocrConfidence}%` }}
                                      ></div>
                                    </div>
                                    <span
                                      className={`text-sm font-medium ${getConfidenceColor(record.ocrConfidence)}`}
                                    >
                                      {record.ocrConfidence}%
                                    </span></div>);
                                    })()}
                                  </span>
                              ) : (
                                <span className="text-sm font-medium text-gray-500">
                                  {record.ocr_processing_status === "pending" && "Pending OCR"}
                                  {record.ocr_processing_status === "error" && "Error"}
                                  {record.ocr_processing_status === "no_assets_found" && "No Asset Available"}
                                </span>
                              )}
                            </td>
                            <td className="px-6 py-4">
                              {record.metadataConfidence > 0 ? (
                                <div className="flex items-center space-x-2">
                                  <div className="flex-1 bg-gray-200 rounded-full h-2">
                                    <div
                                      className={`h-2 rounded-full ${getConfidenceBarColor(record.metadataConfidence)}`}
                                      style={{ width: `${record.metadataConfidence}%` }}
                                    ></div>
                                  </div>
                                  <span
                                    className={`text-sm font-medium ${getConfidenceColor(record.metadataConfidence)}`}
                                  >
                                    {record.metadataConfidence}%
                                  </span>
                                </div>
                              ) : (
                                <span className="text-sm font-medium text-gray-500">—</span>
                              )}
                            </td>
                            <td className="px-6 py-4">{getStatusBadge(record.status, record)}</td>
                            <td className="px-6 py-4">{getResourceTypeBadge(record.resourceType, record.assetCount)}</td>
                            <td className="px-6 py-4 text-sm w-44">
                              {(() => {
                                let by: string | undefined
                                let at: string | undefined
                                if (
                                  (record.status === 'published' || record.status === 'publishing') &&
                                  record.publishedBy
                                ) {
                                  by = record.publishedBy
                                  at = record.publishedAt
                                } else if (record.status === 'reviewed' && record.validatedBy) {
                                  by = record.validatedBy
                                  at = record.validatedAt
                                }
                                if (!by && !at) {
                                  return <span className="text-gray-400">—</span>
                                }
                                return (
                                  <div className="text-center space-y-1">
                                    {by && (
                                      <p className="font-medium text-gray-900">{by}</p>
                                    )}
                                    {at && (
                                      <p className="text-gray-500 text-xs">{formatDate(at)}</p>
                                    )}
                                  </div>
                                )
                              })()}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* Pagination */}
              {pagination.totalItems > 0 && (
                <div className="relative">
                  {isPaginating && (
                    <div className="absolute inset-0 bg-white bg-opacity-75 flex justify-center items-center z-10">
                      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
                      <span className="ml-3 text-gray-600 font-medium">Loading page...</span>
                    </div>
                  )}
                  <Pagination
                    pagination={pagination}
                    onPageChange={handlePageChange}
                    onItemsPerPageChange={handleItemsPerPageChange}
                    isLoading={isPaginating}
                  />
                </div>
              )}
            </>
          )}
        </div>


      {/* Description Popover - Rendered via Portal */}
      {openPopoverId && popoverPosition && records.find(r => r.id === openPopoverId) && (() => {
        const record = records.find(r => r.id === openPopoverId)!
        return createPortal(
          <div
            className="fixed description-popover z-[10001] w-64 p-3 bg-white border border-gray-200 rounded-lg shadow-xl pointer-events-auto"
            style={{
              top: `${popoverPosition.top}px`,
              left: `${popoverPosition.left}px`,
              transform: 'translateY(-100%)',
              marginBottom: '8px',
              maxHeight: '300px',
              overflowY: 'auto'
            }}
            onMouseEnter={() => setOpenPopoverId(record.id)}
            onMouseLeave={() => {
              setOpenPopoverId(null)
              setPopoverPosition(null)
            }}
          >
            <div className="flex items-start justify-between mb-2">
              <h4 className="text-sm font-semibold text-gray-900">Description</h4>
            </div>
            <p className="text-xs text-gray-600">{record.content}</p>
          </div>,
          document.body
        )
      })()}

      {/* Status Popover - Rendered via Portal (only for pending status) */}
      {openStatusPopoverId && statusPopoverPosition && records.find(r => r.id === openStatusPopoverId && r.status === 'pending') && (() => {
        const record = records.find(r => r.id === openStatusPopoverId && r.status === 'pending')!
        const statuses = [
          { label: 'Related Assets', status: record.related_assets_status },
          { label: 'Asset Details', status: record.asset_details_status },
          { label: 'Original Files', status: record.original_file_status },
          { label: 'OCR LLM Batch Processing', status: record.ocr_batch_status },
          { label: 'OCR LLM Batch Result Processing', status: record.ocr_processing_status },
          { label: 'Metadata Extraction', status: record.metadata_extraction_status },
          { label: 'Review', status: 'pending' },
        ]
        
        const allStatuses = statuses
        
        return createPortal(
          <div
            className="fixed status-popover z-[10001] w-64 p-3 bg-white border border-museum-accent rounded-lg shadow-xl pointer-events-auto"
            style={{
              top: `${statusPopoverPosition.top}px`,
              left: `${statusPopoverPosition.left}px`,
              transform: 'translateY(-100%)',
              marginBottom: '8px',
            }}
            onMouseEnter={() => setOpenStatusPopoverId(record.id)}
            onMouseLeave={() => {
              setOpenStatusPopoverId(null)
              setStatusPopoverPosition(null)
            }}
          >
            <div className="flex items-start justify-between mb-2">
              <h4 className="text-sm font-semibold text-gray-900">Processing Status</h4>
            </div>
            <div className="space-y-2">
              {allStatuses.map((item) => (
                <div key={item.label} className="flex items-center gap-2 text-xs">
                  {getStatusIcon(item.status)}
                  <span className="text-gray-700">{item.label}</span>
                </div>
              ))}
            </div>
          </div>,
          document.body
        )
      })()}

      {/* Toast Notification */}
      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={() => setToast(null)}
          />
        </div>
      )}

      {/* Ingest Confirmation Dialogs */}
      <ConfirmDialog
        isOpen={showIngestDialog}
        onClose={() => setShowIngestDialog(false)}
        onConfirm={handleIngestConfirm}
        title="Confirm Data Ingestion"
        message={selectedRecords.size > 0
          ? `Queue ${selectedRecords.size} selected document(s) for ingestion? Only records with a usable Date Published to Portal (TRC) will be queued; others will be skipped and summarized afterward.`
          : `Are you sure you want to queue all document(s) for data ingestion? This will process the documents and make them searchable.`}
        confirmText="Queue for Ingestion"
        cancelText="Cancel"
        variant="info"
        isLoading={isIngesting}
      />

      <ConfirmDialog
        isOpen={showIngestByFilterDialog}
        onClose={() => setShowIngestByFilterDialog(false)}
        onConfirm={handleIngestByFilterConfirm}
        title="Confirm Bulk Ingestion"
        message={
          'Queue all documents matching the current filters for ingestion? ' +
          'Only records with a non-empty Date Published to Portal (TRC) will be included. ' +
          'This may affect a large number of documents.'
        }
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

      {showBulkUnpublishModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onClick={() => {
            if (!isBulkUnpublishing) setShowBulkUnpublishModal(false)
          }}
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-md w-full p-6"
            role="dialog"
            aria-modal="true"
            aria-labelledby="bulk-unpublish-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="bulk-unpublish-title" className="text-lg font-semibold text-gray-900 mb-2">
              Bulk unpublish by filters
            </h3>
            <p className="text-sm text-gray-600 mb-4">
              Only records in <strong>Published</strong> status in this collection will be unpublished.
              Adjust filters to narrow which published records are included.
            </p>
            <div className="space-y-4">
              <div>
                <label htmlFor="bulk-unpublish-resource-type" className="block text-sm font-medium text-gray-700 mb-1">
                  Resource type
                </label>
                <div className="relative">
                  <select
                    id="bulk-unpublish-resource-type"
                    value={bulkUnpublishResourceType}
                    onChange={(e) => setBulkUnpublishResourceType(e.target.value)}
                    className="appearance-none w-full bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-red-500 focus:border-red-500 cursor-pointer"
                  >
                    <option value="">All resource types</option>
                    {resourceTypes.map((rt) => (
                      <option key={rt} value={rt}>
                        {rt}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                </div>
              </div>
              <div>
                <label htmlFor="bulk-unpublish-confidence" className="block text-sm font-medium text-gray-700 mb-1">
                  OCR confidence
                </label>
                <div className="relative">
                  <select
                    id="bulk-unpublish-confidence"
                    value={bulkUnpublishConfidence}
                    onChange={(e) => setBulkUnpublishConfidence(e.target.value)}
                    className="appearance-none w-full bg-white border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm focus:ring-2 focus:ring-red-500 focus:border-red-500 cursor-pointer"
                  >
                    <option value="">All levels</option>
                    <option value="high">Very High (90–100%)</option>
                    <option value="medium">High (70–89%)</option>
                    <option value="low">Medium (50–69%)</option>
                    <option value="very-low">Low (&lt;50%)</option>
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4 pointer-events-none" />
                </div>
              </div>
              <div>
                <label htmlFor="bulk-unpublish-title" className="block text-sm font-medium text-gray-700 mb-1">
                  Title contains
                </label>
                <input
                  id="bulk-unpublish-title"
                  type="text"
                  value={bulkUnpublishTitle}
                  onChange={(e) => setBulkUnpublishTitle(e.target.value)}
                  placeholder="Optional — same as list search"
                  className="w-full border border-gray-300 rounded-lg px-4 py-2 text-sm focus:ring-2 focus:ring-red-500 focus:border-red-500"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6 pt-4 border-t border-gray-200">
              <button
                type="button"
                onClick={() => setShowBulkUnpublishModal(false)}
                disabled={isBulkUnpublishing}
                className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleBulkUnpublishConfirm}
                disabled={isBulkUnpublishing}
                className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors font-medium disabled:opacity-50 inline-flex items-center gap-2"
              >
                {isBulkUnpublishing ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
                    Starting…
                  </>
                ) : (
                  <>
                    <Undo2 className="w-4 h-4" />
                    Unpublish matching
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* OCR Warning Dialog */}
      <ConfirmDialog
        isOpen={showOcrWarningDialog}
        onClose={handleOcrWarningCancel}
        onConfirm={handleOcrWarningConfirm}
        title="OCR Processing Incomplete"
        message="OCR processing is not yet complete for this record. The text content may not be available or may be incomplete. Do you want to proceed anyway?"
        confirmText="Proceed Anyway"
        cancelText="Cancel"
        variant="warning"
        isLoading={false}
      />
    </div>
  )
}

const CollectionsItemsPage: React.FC = () => {
  const { repository, collectionName } = useParams<{ repository: string; collectionName: string }>()
  const decodedRepository = repository ? decodeURIComponent(repository) : ''
  const decodedCollectionName = collectionName ? decodeURIComponent(collectionName) : ''
  
  return (
    <FiltersProvider 
      initialRepository={decodedRepository}
      initialCollectionName={decodedCollectionName}
    >
      <CollectionsItemsPageContent />
    </FiltersProvider>
  )
}

export default CollectionsItemsPage
