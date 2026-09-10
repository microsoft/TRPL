'use client'

import React, { useState, useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'
import Header from '@/components/Header'
import BreadcrumbNav from '@/components/BreadcrumbNav'
import DocumentHeader from '@/components/DocumentHeader'
import CorrectionsPanel, { CorrectionsPanelRef } from '@/components/CorrectionsPanel'
import ActionFooter from '@/components/ActionFooter'
import AuditSidebar from '@/components/AuditSidebar'
import KeyboardShortcutsModal from '@/components/KeyboardShortcutsModal'
import Toast from '@/components/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'
import DocumentConflictDialog from '@/components/DocumentConflictDialog'
import ChangeHistory, { ChangeHistoryRef } from '@/components/ChangeHistory'
import { apiService, ApiDocumentMetadata, AssetDetail } from '@/services/api'
import { handleDocumentSaveError, DOCUMENT_CONFLICT_BANNER_MESSAGE, DOCUMENT_CONFLICT_BANNER_TITLE } from '@/utils/documentConflict'
import { useDocumentSaveQueue } from '@/utils/documentSaveQueue'
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut, RotateCw, Maximize, ImageIcon, X } from 'lucide-react'
import { stripHtml } from '@/utils/stripHtml'
import { useAuth } from '@/contexts/AuthContext'
import DocumentViewer from '@/components/DocumentViewer'
import AuthenticatedBlobImage from '@/components/AuthenticatedBlobImage'
import { mapDocumentFieldsWithStatus, FieldStatusMap } from '@/utils/fieldStatusMapper'
import { hasPortalPublishDateMetadata } from '@/utils/portalPublishDate'
import { Lock } from 'lucide-react'

const ReviewPage: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const { user, permissions } = useAuth()
  const canEdit = permissions.documents.canEdit

  // API Data State
  const [documentData, setDocumentData] = useState<ApiDocumentMetadata | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [apiError, setApiError] = useState<string | null>(null)


  const [currentAssetIndex, setCurrentAssetIndex] = useState(0)
  const [zoomLevel, setZoomLevel] = useState(100)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [imagePosition, setImagePosition] = useState({ x: 0, y: 0 })
  const [isDragging, setIsDragging] = useState(false)
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })
  const imageContainerRef = useRef<HTMLDivElement>(null)

  // Get current asset
  const currentAsset = documentData?.asset_details?.[currentAssetIndex]
  const totalAssets = documentData?.asset_details?.length || 0

  // Get OCR text from current asset
  // ocr_text_original is the pristine original that never changes
  // ocr_text is the working copy that gets modified
  const currentOcrTextOriginal = currentAsset?.ocr_result?.ocr_text_original || ''
  const currentOcrText = currentAsset?.ocr_result?.ocr_text || ''

  // Store initial transcription in a ref for change detection
  const initialTranscriptionRef = useRef(currentOcrText)

  // State
  const [transcription, setTranscription] = useState(currentOcrTextOriginal) // Read-only original
  const [modifiedText, setModifiedText] = useState(currentOcrText) // Editable working copy
  const [visualDescriptionModified, setVisualDescriptionModified] = useState("") // Editable visual description
  const [isModified, setIsModified] = useState(false)
  const [lastSaved, setLastSaved] = useState<Date>(new Date())
  const [showAuditSidebar, setShowAuditSidebar] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)

  // Metadata state
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [creationDate, setCreationDate] = useState('')
  const [creator, setCreator] = useState('')
  const [recipient, setRecipient] = useState('')
  const [citation, setCitation] = useState('')
  const [collection, setCollection] = useState('')
  const [identifier, setIdentifier] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [period, setPeriod] = useState('')
  const [repository, setRepository] = useState('')
  const [rights, setRights] = useState('')
  const [productionMethod, setProductionMethod] = useState('')
  const [language, setLanguage] = useState('')
  const [severeDeviation, setSevereDeviation] = useState(false)
  const [deviationNotes, setDeviationNotes] = useState('')
  const [archivistNotes, setArchivistNotes] = useState('')
  const [markComplete, setMarkComplete] = useState(false)
// Add this state after other state declarations (around line 60)
const [fieldStatus, setFieldStatus] = useState<FieldStatusMap>({})
  // Track original values for change detection
  const originalValuesRef = useRef<{[key: string]: string}>({})
  const etagRef = useRef<string | null>(null)
  const runDocumentSave = useDocumentSaveQueue()
  const changeHistoryRef = useRef<ChangeHistoryRef>(null)
  const correctionsPanelRef = useRef<CorrectionsPanelRef>(null)
  const lastFetchedAssetRef = useRef<{ documentId: string; assetId: string; index: number } | null>(null)
  const [isPublishing, setIsPublishing] = useState(false) //new state for publishing
  const [showPublishConfirm, setShowPublishConfirm] = useState(false) // confirm dialog state
  const [isPublishingInProgress, setIsPublishingInProgress] = useState(false) // loading state for publishing

  // Dirty fields tracking for improved save UX
  const [dirtyFields, setDirtyFields] = useState<Set<string>>(new Set())
  const [isSaving, setIsSaving] = useState(false)
  const [showConflictDialog, setShowConflictDialog] = useState(false)
  const [documentStale, setDocumentStale] = useState(false)
  const [isReloadingAfterConflict, setIsReloadingAfterConflict] = useState(false)

  const onSaveError = (error: unknown, fallbackMessage?: string) => {
    console.error('Save failed:', error)
    handleDocumentSaveError(error, {
      setConflictDialogOpen: (open) => {
        setShowConflictDialog(open)
        if (open) setDocumentStale(true)
      },
      setToast,
      fallbackMessage,
    })
  }

  const handleReloadAfterConflict = () => {
    setIsReloadingAfterConflict(true)
    window.location.reload()
  }

  // Fetch document data from API
  useEffect(() => {
    const fetchDocument = async () => {
      if (!id) {
        setApiError('No document ID provided')
        setIsLoading(false)
        return
      }

      // Reset the last fetched asset ref when document changes
      lastFetchedAssetRef.current = null
      etagRef.current = null

      try {
        setIsLoading(true)
        setApiError(null)
        const data = await apiService.getDocumentById(id)
        setDocumentData(data)

        // Store etag for optimistic concurrency
        etagRef.current = (data as any)._etag || null

        // Set initial transcription from first asset
        if (data.asset_details && data.asset_details.length > 0) {
          const firstOcrTextOriginal = data.asset_details[0]?.ocr_result?.ocr_text_original || ''
          const firstOcrText = data.asset_details[0]?.ocr_result?.ocr_text || ''
          setTranscription(firstOcrTextOriginal) // Read-only original
          setModifiedText(firstOcrText) // Editable working copy
          initialTranscriptionRef.current = firstOcrText
        }

        // Set initial visual description
        setVisualDescriptionModified(data.visual_detailed_description_flexible || '')

        // Populate metadata fields from API response
        if (data.metadata || data.extracted_metadata) {
          const { values, status } = mapDocumentFieldsWithStatus(data)
          
          // Set all field values from the mapper
          setTitle(values.title)
          setDescription(values.description)
          setCreationDate(values.creationDate)
          setCreator(values.creator)
          setRecipient(values.recipient)
          setCitation(values.citation)
          setResourceType(values.resourceType)
          setPeriod(values.period)
          setRights(values.rights)
          setProductionMethod(values.productionMethod)
          setLanguage(values.language)

          // Set field status
          setFieldStatus(status)

          // Set additional fields that aren't in the mapper
          setCollection(data.metadata?.Collection || '')
          setIdentifier(data.metadata?.["Source Record ID"]|| '')
          setRepository(data.metadata?.Repository || '')
          
          // Store original values for change detection
          originalValuesRef.current = {
            title: values.title,
            description: values.description,
            creationDate: values.creationDate,
            creator: values.creator,
            recipient: values.recipient,
            citation: values.citation,
            resourceType: values.resourceType,
            period: values.period,
            rights: values.rights,
            productionMethod: values.productionMethod,
            language: values.language
          }
        }

        // Load archivist fields
        setArchivistNotes(data.archivist_notes || '')
        // setMarkComplete(data.archivist_status === 'Reviewed' || data.archivist_status === 'reviewed')
        const archivistStatus = (data.archivist_status || '').toLowerCase()
        setMarkComplete(archivistStatus === 'reviewed' || archivistStatus === 'publishing')
        setIsPublishing(archivistStatus === 'publishing')
      } catch (error) {
        console.error('Failed to fetch document:', error)
        setApiError('Failed to load document from API')
        setToast({
          type: 'error',
          message: 'Could not load document. Please try again.'
        })
      } finally {
        setIsLoading(false)
      }
    }

    fetchDocument()
  }, [id])

  // Fetch asset details when currentAssetIndex changes or when documentData is first loaded
  useEffect(() => {
    const fetchAssetDetails = async () => {
      if (!id || !documentData?.asset_details || currentAssetIndex < 0) return

      const asset = documentData.asset_details[currentAssetIndex]
      if (!asset) return

      // Get asset_id (can be stored as 'asset_id' or 'id' depending on ingestion)
      const assetId = asset.asset_id

      if (!assetId) {
        // No asset_id available, skip fetching
        return
      }

      // Check if we've already fetched this asset to avoid duplicate API calls
      const lastFetched = lastFetchedAssetRef.current
      if (lastFetched && 
          lastFetched.documentId === id && 
          lastFetched.assetId === assetId && 
          lastFetched.index === currentAssetIndex) {
        // Already fetched this asset, skip
        return
      }

      try {
        // Fetch the asset details from the API
        const assetDetails = await apiService.getAssetById(id, assetId)

        // Update the last fetched reference
        lastFetchedAssetRef.current = { documentId: id, assetId, index: currentAssetIndex }

        // Update the asset in documentData with the fetched details
        setDocumentData(prevData => {
          if (!prevData?.asset_details) return prevData

          const updatedAssetDetails = [...prevData.asset_details]
          updatedAssetDetails[currentAssetIndex] = assetDetails

          return {
            ...prevData,
            asset_details: updatedAssetDetails
          }
        })
      } catch (error) {
        console.error('Failed to fetch asset details:', error)
        setToast({
          type: 'error',
          message: `Failed to load asset details: ${error instanceof Error ? error.message : 'Unknown error'}`
        })
      }
    }

    fetchAssetDetails()
  }, [currentAssetIndex, id, documentData])

  // Update transcription when asset changes
  useEffect(() => {
    if (currentAsset?.ocr_result) {
      const ocrTextOriginal = currentAsset.ocr_result.ocr_text_original || ''
      const ocrText = currentAsset.ocr_result.ocr_text || ''
      setTranscription(ocrTextOriginal) // Read-only original
      setModifiedText(ocrText) // Editable working copy
      initialTranscriptionRef.current = ocrText
      setIsModified(false)
    }
    // Reset zoom and position when changing assets
    setZoomLevel(100)
    setImagePosition({ x: 0, y: 0 })
  }, [currentAssetIndex, currentAsset])

  // Navigation handlers
  const handlePreviousAsset = () => {
    if (currentAssetIndex > 0) {
      setCurrentAssetIndex(prev => prev - 1)
    }
  }

  const handleNextAsset = () => {
    if (currentAssetIndex < totalAssets - 1) {
      setCurrentAssetIndex(prev => prev + 1)
    }
  }

  const handleAssetSelect = (index: number) => {
    setCurrentAssetIndex(index)
  }

  // Helper function to check if record is published
  const isRecordPublished = (): boolean => {
    if (!documentData) return false
    const archivistStatus = (documentData.archivist_status || '').toLowerCase()
    return archivistStatus === 'published'
  }

  // Handle metadata field blur - send changes to API
  const handleFieldBlur = async (fieldName: string, currentValue: string) => {
    if (!id) return

    const originalValue = originalValuesRef.current[fieldName] || ''

    // Only send update if value actually changed
    if (originalValue.trim() === currentValue.trim()) {
      // Clear from dirty fields even if no change
      setDirtyFields(prev => {
        const next = new Set(prev)
        next.delete(fieldName)
        return next
      })
      return
    }

    try {
      await runDocumentSave(async () => {
      // Check if record is published - if so, change status to pending
      const wasPublished = isRecordPublished()
      
      // Prepare update request with only the changed field
      // user_id and user_display are now injected by the backend from authentication
      const updateRequest: any = {
        metadata: {
          [fieldName]: currentValue
        }
      }

      // If record was published, change status to pending
      if (wasPublished) {
        updateRequest.archivist_status = 'pending'
      }

      // Send update to API
      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      )

      // Update etag for next update
      etagRef.current = (updatedDoc as any)._etag || null

      // Update original value
      originalValuesRef.current[fieldName] = currentValue

      // Update local state if status changed
      if (wasPublished) {
        setDocumentData(prevData => {
          if (!prevData) return prevData
          return {
            ...prevData,
            archivist_status: 'pending'
          }
        })
        setMarkComplete(false)
        setIsPublishing(false)
      }

      // Clear from dirty fields after successful save
      setDirtyFields(prev => {
        const next = new Set(prev)
        next.delete(fieldName)
        return next
      })

      // Silent success - no toast (modern UX pattern)

      // Refresh audit history
      if (changeHistoryRef.current) {
        console.log('Refreshing audit history after field update...')
        await changeHistoryRef.current.refetch()
      } else {
        console.warn('Change history ref is not available')
      }

      // Refresh last saved timestamp in CorrectionsPanel
      if (correctionsPanelRef.current) {
        console.log('Refreshing last saved timestamp...')
        await correctionsPanelRef.current.refreshLastSaved()
      } else {
        console.warn('Corrections panel ref is not available')
      }
      })

    } catch (error) {
      onSaveError(error, `Failed to update ${fieldName}`)
      // Keep field in dirty set on error so user can retry
      throw error
    }
  }

  // Handle archivist notes blur - send changes to API
  const handleArchivistNotesBlur = async (notes: string) => {
    if (!id) return

    try {
      await runDocumentSave(async () => {
      // Check if record is published - if so, change status to pending
      const wasPublished = isRecordPublished()
      
      // user_id and user_display are now injected by the backend from authentication
      const updateRequest: any = {
        archivist_notes: notes
      }

      // If record was published, change status to pending
      if (wasPublished) {
        updateRequest.archivist_status = 'pending'
      }

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      )

      etagRef.current = (updatedDoc as any)._etag || null

      // Update local state if status changed
      if (wasPublished) {
        setDocumentData(prevData => {
          if (!prevData) return prevData
          return {
            ...prevData,
            archivist_status: 'pending'
          }
        })
        setMarkComplete(false)
        setIsPublishing(false)
      }

      // Clear from dirty fields after successful save
      setDirtyFields(prev => {
        const next = new Set(prev)
        next.delete('archivistNotes')
        return next
      })

      // Silent success - no toast (modern UX pattern)

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch()
      }

      // Refresh last saved timestamp in CorrectionsPanel
      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved()
      }
      })
    } catch (error) {
      onSaveError(error, 'Failed to update archivist notes')
      // Keep field in dirty set on error
      throw error
    }
  }

  // Helper function to get current field value
  const getFieldValue = (fieldName: string): string => {
    const fieldMap: Record<string, string> = {
      title,
      description,
      creationDate,
      creator,
      recipient,
      citation,
      resourceType,
      period,
      rights,
      productionMethod,
      language,
      archivistNotes,
      modifiedText
    }
    return fieldMap[fieldName] || ''
  }

  // Save all dirty fields at once
  const handleSaveAll = async () => {
    if (dirtyFields.size === 0 || !id) return

    setIsSaving(true)

    try {
      // Save dirty fields sequentially so each request uses a fresh ETag
      for (const fieldName of Array.from(dirtyFields)) {
        const currentValue = getFieldValue(fieldName)

        if (fieldName === 'modifiedText') {
          await handleOcrBlur(currentValue)
        } else if (fieldName === 'archivistNotes') {
          await handleArchivistNotesBlur(currentValue)
        } else {
          await handleFieldBlur(fieldName, currentValue)
        }
      }

      // Clear dirty fields
      setDirtyFields(new Set())

      // Silent success - no toast (modern UX pattern)

    } catch (error) {
      throw error
    } finally {
      setIsSaving(false)
    }
  }

  const hasPortalPublishDate = (): boolean =>
    hasPortalPublishDateMetadata(documentData?.metadata?.['Date Published to Portal'])

  // Handle approval for publishing - show confirm dialog
  const handleApproveForPublishingClick = () => {
    if (!hasPortalPublishDate()) {
      setToast({
        type: 'error',
        message: 'Cannot publish since there is no TRC object'
      })
      return
    }
    setShowPublishConfirm(true)
  }

  // Actual publishing logic after confirmation
  const handleConfirmPublishing = async () => {
    if (!id) {
      setShowPublishConfirm(false)
      return
    }

    if (!hasPortalPublishDate()) {
      setToast({
        type: 'error',
        message: 'Cannot publish since there is no TRC object'
      })
      setShowPublishConfirm(false)
      return
    }

    setIsPublishingInProgress(true)
    try {
      const updateRequest = {
        archivist_status: 'publishing'
      }

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      )

      etagRef.current = (updatedDoc as any)._etag || null

      // Update local state
      setIsPublishing(true)
      setDocumentData(prevData => {
        if (!prevData) return prevData
        return {
          ...prevData,
          archivist_status: 'publishing'
        }
      })

      // Minimal feedback for important action
      setToast({
        type: 'info',
        message: 'Document approved for publishing'
      })

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch()
      }

      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved()
      }
    } catch (error) {
      onSaveError(error, 'Failed to approve document for publishing')
    } finally {
      setIsPublishingInProgress(false)
      setShowPublishConfirm(false)
    }
  }

  // Handle undo publishing
  const handleUndoPublishing = async () => {
    if (!id) return

    try {
      const updateRequest = {
        archivist_status: 'reviewed'
      }

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      )

      etagRef.current = (updatedDoc as any)._etag || null

    // Update local state
      setIsPublishing(false)
      if (documentData) {
        setDocumentData({
          ...documentData,
          archivist_status: 'reviewed'
        })
      }

      // Minimal feedback for important action
      setToast({
        type: 'info',
        message: 'Publishing approval undone'
      })

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch()
      }

      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved()
      }
    } catch (error) {
      onSaveError(error, 'Failed to undo publishing approval')
    }
  }

  // Handle mark as complete change - send changes to API
  const handleMarkCompleteChange = async (isComplete: boolean) => {
    if (!id) return

    setMarkComplete(isComplete)
    // setIsModified(true)

    try {
      // user_id and user_display are now injected by the backend from authentication
      // validated_by/published_by are set by the backend based on status
      const updateRequest = {
        archivist_status: isComplete ? 'reviewed' : 'pending'
      }

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      )

      etagRef.current = (updatedDoc as any)._etag || null
      if (documentData) {
        setDocumentData({
          ...documentData,
          archivist_status: isComplete ? 'reviewed' : 'pending'
        })
      }
      // Silent success for status change - user can see the status badge
      // No toast needed

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch()
      }

      // Refresh last saved timestamp in CorrectionsPanel
      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved()
      }
    } catch (error) {
      onSaveError(error, 'Failed to update completion status')
      // Revert the checkbox state
      setMarkComplete(!isComplete)
    }
  }

  // Handle OCR text blur - send changes to API
  const handleVisualDescriptionBlur = async (visualDesc: string) => {
    if (!id || !documentData) return;

    const currentVisualDesc = documentData.visual_detailed_description_flexible || "";
    if (visualDesc === currentVisualDesc) {
      // No change, remove from dirty fields
      setDirtyFields((prev) => {
        const newSet = new Set(prev);
        newSet.delete("visualDescriptionModified");
        return newSet;
      });
      return;
    }

    try {
      setIsSaving(true);
      await runDocumentSave(async () => {
      const updateRequest = {
        visual_detailed_description_flexible: visualDesc,
        correlation_id: `visual-desc-update-${Date.now()}`,
      };

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      );

      if (updatedDoc) {
        // Re-fetch so asset blob URLs always carry fresh SAS tokens for the viewer.
        const freshDoc = await apiService.getDocumentById(id)
        lastFetchedAssetRef.current = null
        setDocumentData(freshDoc)
        etagRef.current = (freshDoc as any)._etag || (updatedDoc as any)._etag || null
        setDirtyFields((prev) => {
          const newSet = new Set(prev);
          newSet.delete("visualDescriptionModified");
          return newSet;
        });
        setToast({
          type: "success",
          message: "Visual description saved successfully",
        });
        if (correctionsPanelRef.current) {
          correctionsPanelRef.current.refreshLastSaved();
        }
      }
      })
    } catch (error: unknown) {
      onSaveError(error, 'Failed to save visual description')
      throw error
    } finally {
      setIsSaving(false);
    }
  };

  const handleOcrBlur = async (ocrText: string) => {
    if (!id || currentAssetIndex === undefined) return

    const originalOcr = initialTranscriptionRef.current || ''

    // Only send update if value actually changed
    if (originalOcr.trim() === ocrText.trim()) {
      // Clear from dirty fields even if no change
      setDirtyFields(prev => {
        const next = new Set(prev)
        next.delete('modifiedText')
        return next
      })
      return
    }

    try {
      await runDocumentSave(async () => {
      // Check if record is published - if so, change status to pending
      const wasPublished = isRecordPublished()
      
      // user_id and user_display are now injected by the backend from authentication
      const ocrRequest: any = {
        asset_index: currentAssetIndex,
        ocr_text: ocrText
      }

      const asset = documentData?.asset_details?.[currentAssetIndex]
      const assetKey = asset?.asset_id || asset?.id || ''

      // Update OCR first to get the new ETag
      const updatedDoc = await apiService.updateOcrText(
        id,
        assetKey,
        ocrRequest,
        etagRef.current || undefined
      )

      // Re-fetch so etag and blob URLs stay in sync (same pattern as visual save)
      const freshDoc = await apiService.getDocumentById(id)
      etagRef.current = freshDoc._etag ?? updatedDoc._etag ?? null

      const savedOcrText =
        freshDoc.asset_details?.[currentAssetIndex]?.ocr_result?.ocr_text
        ?? updatedDoc.asset_details?.[currentAssetIndex]?.ocr_result?.ocr_text
        ?? ocrText
      setModifiedText(savedOcrText)
      initialTranscriptionRef.current = savedOcrText
      setDocumentData((prevData) => {
        if (!prevData?.asset_details || !freshDoc.asset_details?.[currentAssetIndex]) {
          return freshDoc
        }
        const nextAssets = [...prevData.asset_details]
        nextAssets[currentAssetIndex] = freshDoc.asset_details[currentAssetIndex]
        return { ...prevData, ...freshDoc, asset_details: nextAssets }
      })
      lastFetchedAssetRef.current = null

      // If record was published, update status using the new ETag
      if (wasPublished) {
        const statusUpdatedDoc = await apiService.updateDocumentMetadata(
          id,
          { archivist_status: 'pending' },
          etagRef.current || undefined
        )

        etagRef.current = statusUpdatedDoc._etag || null

        setDocumentData(prevData => {
          if (!prevData) return prevData
          return {
            ...prevData,
            archivist_status: 'pending'
          }
        })
        setMarkComplete(false)
        setIsPublishing(false)
      }

      // Clear from dirty fields after successful save
      setDirtyFields(prev => {
        const next = new Set(prev)
        next.delete('modifiedText')
        return next
      })

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch()
      }

      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved()
      }
      })
    } catch (error) {
      onSaveError(error, 'Failed to update OCR text')
      throw error
    }
  }

  // Zoom handlers
  const handleZoomIn = () => {
    setZoomLevel(prev => Math.min(prev + 25, 1000))
  }

  const handleZoomOut = () => {
    setZoomLevel(prev => Math.max(prev - 25, 50))
  }

  // Reset zoom and position
  const handleResetZoom = () => {
    setZoomLevel(100)
    setImagePosition({ x: 0, y: 0 })
  }

  // Drag handlers for panning
  const handleMouseDown = (e: React.MouseEvent) => {
    if (zoomLevel > 100) {
      setIsDragging(true)
      setDragStart({
        x: e.clientX - imagePosition.x,
        y: e.clientY - imagePosition.y
      })
      e.preventDefault()
    }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging && zoomLevel > 100) {
      setImagePosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y
      })
    }
  }

  const handleMouseUp = () => {
    setIsDragging(false)
  }

  const handleMouseLeave = () => {
    setIsDragging(false)
  }

  // Fullscreen handlers
  const handleOpenFullscreen = () => {
    setIsFullscreen(true)
  }

  const handleCloseFullscreen = () => {
    setIsFullscreen(false)
  }

  // Handle ESC key to close fullscreen
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) {
        handleCloseFullscreen()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isFullscreen])

  // Warn on unsaved changes before browser navigation (refresh, close, etc.)
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirtyFields.size > 0 || isSaving) {
        const message = `You have ${dirtyFields.size} unsaved ${dirtyFields.size === 1 ? 'change' : 'changes'}. Your changes will be lost.`
        e.preventDefault()
        e.returnValue = message // Required for Chrome
        return message // Required for some browsers
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [dirtyFields, isSaving])

  // TODO: Implement navigation blocking for React Router
  // Note: useBlocker requires React Router v6.4+ data router (createBrowserRouter)
  // For now, we rely on beforeunload event for browser navigation protection
  // React Router in-app navigation will need to be handled differently or
  // the app needs to be upgraded to use createBrowserRouter

  // Keyboard shortcut: Ctrl+S / Cmd+S to save
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        if (dirtyFields.size > 0 && !isSaving) {
          handleSaveAll()
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [dirtyFields, isSaving])

  // Save function
  const handleSave = async (markComplete = false) => {
    try {
      await handleSaveAll()
      setIsModified(false)
      setLastSaved(new Date())
      setToast({
        type: 'success',
        message: markComplete ? 'Document Reviewed and saved' : 'Changes saved successfully'
      })
    } catch {
      // Save handlers already show the conflict dialog or error toast.
    }
  }

  // Save & Complete
  const handleSaveAndComplete = async () => {
    if (!transcription.trim()) {
      setToast({ type: 'error', message: 'Please fill transcription before completing' })
      return
    }
    await handleSave(true)
  }

  const breadcrumbItems = [
    { label: 'Home', href: '/' },
    { label: 'Review Queue', href: '/review-queue' },
    { label: 'Review', href: '#' } 
  ]

  return (
    <div className="flex flex-col min-h-screen bg-museum-50">
      <Header
        currentPage="review"
        onNotificationClick={() => {}}
        onHelpClick={() => setShowShortcuts(true)}
        onUserMenuClick={() => {}}
      />

      {/* <BreadcrumbNav  /> */}

      <DocumentHeader
        title={title}
        creationDate={creationDate}
        creator={creator}
        collection={collection}
        repository={repository}
        identifier={identifier}
        resourceType={resourceType}
        severeDeviation={severeDeviation}
        status={(() => {
          const archivistStatus = (documentData?.archivist_status || '').trim()
          const normalized = archivistStatus.toLowerCase()

          if (normalized === 'published') {
            return 'Published'
          } else if (normalized === 'publishing') {
            return 'Publishing'
          } else if (normalized === 'reviewed') {
            return 'Reviewed'
          } else if (normalized === 'failed' || normalized === 'error') {
            return 'Failed'
          } else {
            return 'Pending'
          }
        })()}
        onSave={() => handleSave(false)}
        onSaveAndComplete={handleSaveAndComplete}
        datePublishedToPortal={documentData?.metadata?.['Date Published to Portal']}
        sourceRecordId={documentData?.metadata?.['Source Record ID']}
      />

      {/* Read-only notice for users without edit permission */}
      {!canEdit && (
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-2">
          <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
            <Lock className="w-4 h-4 flex-shrink-0" />
            <span>You have read-only access. Only Admin or Archivist users can edit, approve, and ingest documents.</span>
          </div>
        </div>
      )}

      {documentStale && (
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-2">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 text-sm text-amber-900 bg-amber-50 border border-amber-300 rounded-lg px-4 py-3">
            <div>
              <p className="font-medium">{DOCUMENT_CONFLICT_BANNER_TITLE}</p>
              <p className="text-amber-800 mt-1">
                {DOCUMENT_CONFLICT_BANNER_MESSAGE}
              </p>
            </div>
            <button
              type="button"
              onClick={handleReloadAfterConflict}
              disabled={isReloadingAfterConflict}
              className="shrink-0 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 transition-colors"
            >
              {isReloadingAfterConflict ? 'Refreshing…' : 'Refresh tab'}
            </button>
          </div>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div className="flex justify-center items-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-museum-600"></div>
          <span className="ml-4 text-museum-600">Loading document...</span>
        </div>
      )}

      {/* Error State */}
      {apiError && !isLoading && (
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-4">
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-red-800">{apiError}</p>
          </div>
        </div>
      )}

      {/* Content - only show when not loading */}
      {!isLoading && (
        <>
          {/* Top: Source + AI Transcription */}
          <div className="flex flex-col flex-1 overflow-hidden">
            <div className="flex h-[calc(100vh-200px)] overflow-hidden border-b border-museum-200">

            {/* Image Navigation Controls - Left Sidebar */}
              {totalAssets > 0 && (
                <div className="w-1/7 border-r border-museum-200 p-3 bg-white overflow-y-auto ocr-scrollbar">

                  {/* Thumbnail Strip - Vertical */}
                  <div className="flex flex-col space-y-2">
                    {documentData?.asset_details?.map((asset, index) => (
                      <button
                        key={index}
                        className={`relative w-full rounded overflow-hidden border-2 transition-all ${
                          currentAssetIndex === index
                            ? 'border-museum-800 ring-2 ring-museum-800 ring-offset-1'
                            : 'border-museum-300 hover:border-museum-500'
                        }`}
                        onClick={() => handleAssetSelect(index)}
                        title={asset.metadata?.["File Name"] || `Asset ${index + 1}`}
                      >
                        <div className="aspect-(11/12) bg-museum-50 flex items-center justify-center">
                          {asset.blob_thumbnail_url ? (
                            <AuthenticatedBlobImage 
                              src={asset.blob_thumbnail_url} 
                              alt={`Thumbnail ${index + 1}`}
                              className="w-full h-full object-contain"
                            />
                          ) : (
                            <span className="text-xs text-museum-400">No Asset</span>
                          )}
                        </div>
                        <div className={`absolute bottom-0 left-0 right-0 px-1 py-0.5 text-xs font-medium text-center ${
                          currentAssetIndex === index
                            ? 'bg-museum-800 text-white'
                            : 'bg-museum-100 text-museum-600'
                        }`}>
                          {asset.metadata?.["File Name"] || `${index + 1}`}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}


              {/* Source Material */}
              <div className="w-3/7 overflow-y-auto border-r border-museum-200 flex flex-col">
                {/* Image Controls Header */}
                <div className="flex items-center justify-between px-3 py-1.5 border-b border-museum-200 bg-museum-50">
                  {/* Page Navigation */}
                  <div className="flex items-center space-x-3">
                    <button
                      onClick={handlePreviousAsset}
                      disabled={currentAssetIndex === 0 || totalAssets === 0}
                      className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors disabled:text-museum-300 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                      title="Previous Page"
                    >
                      <ChevronLeft className="w-5 h-5" />
                    </button>
                    <span className="text-sm font-medium text-museum-700 min-w-[100px] text-center">
                      {totalAssets > 0 ? currentAsset?.metadata?.["File Name"] ||`Asset ${currentAssetIndex + 1}` : 'No Assets'}
                    </span>
                    <button
                      onClick={handleNextAsset}
                      disabled={currentAssetIndex >= totalAssets - 1 || totalAssets === 0}
                      className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors disabled:text-museum-300 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                      title="Next Image"
                    >
                      <ChevronRight className="w-5 h-5" />
                    </button>
                  </div>

                  {/* Zoom Controls */}
                  <div className="flex items-center space-x-2">
                    <button 
                      className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
                      title="Zoom Out"
                      onClick={handleZoomOut}
                    >
                      <ZoomOut className="w-4 h-4" />
                    </button>

                    <button 
                      className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
                      title="Zoom In"
                      onClick={handleZoomIn}
                    >
                      <ZoomIn className="w-4 h-4" />
                    </button>
                    <button 
                      className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
                      title="Reset Zoom"
                      onClick={handleResetZoom}
                    >
                      <RotateCw className="w-4 h-4" />
                    </button>
                    <button 
                      className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
                      title="Fullscreen"
                      onClick={handleOpenFullscreen}
                      disabled={!currentAsset?.blob_url}
                    >
                      <Maximize className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Image Display */}
                <div 
                  ref={imageContainerRef}
                  className={`flex-1 bg-museum-100 overflow-y-auto ocr-scrollbar ${
                    zoomLevel > 100 ? (isDragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-default'
                  }`}
                  onMouseDown={handleMouseDown}
                  onMouseMove={handleMouseMove}
                  onMouseUp={handleMouseUp}
                  onMouseLeave={handleMouseLeave}
                >
                  {currentAsset?.blob_url ? (
                    // <div className="bg-museum-50 shadow-sm h-full flex items-center justify-center">
                    //   {/* <img 
                    //     src={currentAsset.blob_url} 
                    //     alt={`Asset ${currentAssetIndex + 1}`} 
                    //     className="max-w-full max-h-full rounded-lg shadow-lg object-contain select-none"
                    //     style={{ 
                    //       transform: `scale(${zoomLevel / 100}) translate(${imagePosition.x / (zoomLevel / 100)}px, ${imagePosition.y / (zoomLevel / 100)}px)`,
                    //       transition: isDragging ? 'none' : 'transform 0.2s ease-out'
                    //     }}
                    //     draggable={false}
                    //   /> */}

                    // </div>
                    <DocumentViewer
                      key={currentAsset.blob_url}
                      fileUrl={currentAsset.blob_url}
                      zoomLevel={zoomLevel}
                      imagePosition={imagePosition}
                      isDragging={isDragging}
                    />
                  ) : (
                    <div className="bg-white rounded-lg shadow-sm h-full flex items-center justify-center">
                      <p className="text-museum-500">Assets not available</p>
                    </div>
                  )}
                </div>

                {/* Image Navigation Controls */}
                {/* {totalAssets > 0 && (
                  <div className="border-t border-museum-200 p-3 bg-white">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-museum-700">
                        Image {currentAssetIndex + 1} of {totalAssets}
                      </span>
                      <div className="flex items-center space-x-2">
                        <button
                          onClick={handlePreviousAsset}
                          disabled={currentAssetIndex === 0}
                          className="p-1 text-museum-500 hover:text-museum-700 disabled:text-museum-300 disabled:cursor-not-allowed transition-colors"
                          title="Previous Image"
                        >
                          <ChevronLeft className="w-5 h-5" />
                        </button>
                        <button
                          onClick={handleNextAsset}
                          disabled={currentAssetIndex >= totalAssets - 1}
                          className="p-1 text-museum-500 hover:text-museum-700 disabled:text-museum-300 disabled:cursor-not-allowed transition-colors"
                          title="Next Image"
                        >
                          <ChevronRight className="w-5 h-5" />
                        </button>
                      </div>
                    </div> */}
                    {/* Thumbnail Strip */}
                    {/* <div className="flex items-center space-x-2 overflow-x-auto custom-scrollbar">
                      {documentData?.asset_details?.map((asset, index) => (
                        <button
                          key={index}
                          className={`w-6 h-8 rounded flex items-center justify-center text-xs font-medium border-2 flex-shrink-0 ${
                            currentAssetIndex === index
                              ? 'bg-museum-800 text-white border-museum-800'
                              : 'bg-museum-100 border-museum-300 text-museum-600 hover:bg-museum-200'
                          }`}
                          onClick={() => handleAssetSelect(index)}
                          title={asset.filename || `Image ${index + 1}`}
                        >
                          {index + 1}
                        </button>
                      ))}
                    </div> */}
                  {/* </div>
                )} */}
              </div>

              {/* Human Corrections Panel */}
              <div className="w-3/7 overflow-y-auto">
                <CorrectionsPanel
                  ref={correctionsPanelRef}
                  documentId={id || ''} 
                  transcription={transcription}
                  onTranscriptionChange={() => {}} // Read-only, no changes allowed
                  modifiedText={modifiedText}
                  onModifiedTextChange={value => { 
                    setModifiedText(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('modifiedText'))
                  }}
                  onModifiedTextBlur={handleOcrBlur}
                  visualDescriptionModified={visualDescriptionModified}
                  onVisualDescriptionChange={value => { 
                    setVisualDescriptionModified(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('visualDescriptionModified'))
                  }}
                  onVisualDescriptionBlur={handleVisualDescriptionBlur}
                  title={title}
                  onTitleChange={value => { 
                    setTitle(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('title'))
                  }}
                  onTitleBlur={(value) => handleFieldBlur('title', value)}
                  description={description}
                  onDescriptionChange={value => { 
                    setDescription(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('description'))
                  }}
                  onDescriptionBlur={(value) => handleFieldBlur('description', value)}
                  creationDate={creationDate}
                  onCreationDateChange={value => { 
                    setCreationDate(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('creationDate'))
                  }}
                  onCreationDateBlur={(value) => handleFieldBlur('creationDate', value)}
                  creator={creator}
                  onCreatorChange={value => { 
                    setCreator(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('creator'))
                  }}
                  onCreatorBlur={(value) => handleFieldBlur('creator', value)}
                  recipient={recipient}
                  onRecipientChange={value => { 
                    setRecipient(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('recipient'))
                  }}
                  onRecipientBlur={(value) => handleFieldBlur('recipient', value)}
                  citation={citation}
                  onCitationChange={value => { 
                    setCitation(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('citation'))
                  }}
                  onCitationBlur={(value) => handleFieldBlur('citation', value)}
                  resourceType={resourceType}
                  onResourceTypeChange={value => { 
                    setResourceType(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('resourceType'))
                  }}
                  onResourceTypeBlur={(value) => handleFieldBlur('resourceType', value)}
                  period={period}
                  onPeriodChange={value => { 
                    setPeriod(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('period'))
                  }}
                  onPeriodBlur={(value) => handleFieldBlur('period', value)}
                  repository={repository}
                  onRepositoryChange={value => { 
                    setRepository(value)
                    setIsModified(true)
                  }}
                  rights={rights}
                  onRightsChange={value => { 
                    setRights(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('rights'))
                  }}
                  onRightsBlur={(value) => handleFieldBlur('rights', value)}
                  productionMethod={productionMethod}
                  onProductionMethodChange={value => { 
                    setProductionMethod(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('productionMethod'))
                  }}
                  onProductionMethodBlur={(value) => handleFieldBlur('productionMethod', value)}
                  language={language}
                  onLanguageChange={value => { 
                    setLanguage(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('language'))
                  }}
                  onLanguageBlur={(value) => handleFieldBlur('language', value)}
                  severeDeviation={severeDeviation}
                  onSevereDeviationChange={value => { 
                    setSevereDeviation(value)
                    setIsModified(true)
                  }}
                  deviationNotes={deviationNotes}
                  onDeviationNotesChange={value => { 
                    setDeviationNotes(value)
                    setIsModified(true)
                  }}
                  archivistNotes={archivistNotes}
                  onArchivistNotesChange={value => { 
                    setArchivistNotes(value)
                    setIsModified(true)
                    setDirtyFields(prev => new Set(prev).add('archivistNotes'))
                  }}
                  onArchivistNotesBlur={handleArchivistNotesBlur}
                  markComplete={markComplete}
                  onMarkCompleteChange={handleMarkCompleteChange}
                  onFormChange={() => setIsModified(true)}
                  isPublishing={isPublishing}
                  onApproveForPublishing={handleApproveForPublishingClick}
                  onUndoPublishing={handleUndoPublishing}
                  compareOriginalText={currentOcrTextOriginal}
                  dirtyFields={dirtyFields}
                  compareModifiedText={modifiedText}
                  // fieldStatus={fieldStatus}
                  apiDocument={documentData}
                  isPublished={isRecordPublished()}
                  readOnly={!canEdit}
                />
              </div>
            </div>
          </div>

          {/* Change History Section */}
          {id && <ChangeHistory ref={changeHistoryRef} documentId={id} />}
        </>
      )}


      {/* Footer */}
      {/* <ActionFooter
        isModified={isModified}
        lastSaved={lastSaved}
        onSave={() => handleSave(false)}
        onSaveAndComplete={handleSaveAndComplete}
      /> */}

      {/* Modals & Sidebar */}
      <AuditSidebar isOpen={showAuditSidebar} onClose={() => setShowAuditSidebar(false)} documentId={id || ''} />
      <KeyboardShortcutsModal isOpen={showShortcuts} onClose={() => setShowShortcuts(false)} />

      {/* Publishing Confirm Dialog */}
      <DocumentConflictDialog
        isOpen={showConflictDialog}
        onClose={() => setShowConflictDialog(false)}
        onReload={handleReloadAfterConflict}
        isReloading={isReloadingAfterConflict}
      />

      <ConfirmDialog
        isOpen={showPublishConfirm}
        onClose={() => setShowPublishConfirm(false)}
        onConfirm={handleConfirmPublishing}
        title="Approve for Publishing"
        message="This will mark the document as ready for publication. The document will be queued for publishing and processed automatically. Are you sure you want to proceed?"
        confirmText="Approve for Publishing"
        cancelText="Cancel"
        variant="info"
        isLoading={isPublishingInProgress}
      />

      {/* Toast */}
      {toast && (
        <Toast 
          type={toast.type} 
          message={toast.message} 
          onClose={() => setToast(null)} 
        />
      )}

      {/* Fullscreen Image Modal */}
      {isFullscreen && currentAsset?.blob_url && (
        <div 
          className="fixed inset-0 z-50 bg-black bg-opacity-95 flex items-center justify-center"
          onClick={handleCloseFullscreen}
        >
          <button
            onClick={handleCloseFullscreen}
            className="absolute top-4 right-4 p-2 text-white hover:text-museum-200 bg-black bg-opacity-50 hover:bg-opacity-70 rounded-full transition-all z-10"
            title="Close Fullscreen (ESC)"
          >
            <svg 
              xmlns="http://www.w3.org/2000/svg" 
              className="h-8 w-8" 
              fill="none" 
              viewBox="0 0 24 24" 
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <div onClick={(e) => e.stopPropagation()}>
            <AuthenticatedBlobImage 
              src={currentAsset.blob_url} 
              alt={`Asset ${currentAssetIndex + 1} - Fullscreen`}
              className="max-w-full max-h-full object-contain p-4"
            />
          </div>
        </div>
      )}
    </div>
  )
}

export default ReviewPage