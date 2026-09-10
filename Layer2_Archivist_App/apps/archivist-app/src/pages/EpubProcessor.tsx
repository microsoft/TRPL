'use client'

import React, { useState, useCallback, useEffect, useMemo } from 'react'
import { 
  Eye,
  Loader2,
  Play,
  CheckCircle2,
  Lock
} from 'lucide-react'
import Toast from '@/components/Toast'
import { apiService, EpubDuplicateError } from '@/services/api'
import ConfirmDialog from '@/components/ConfirmDialog'
import PageHeader from '@/components/PageHeader'
import { useAuth } from '@/contexts/AuthContext'
import {
  PipelineStats,
  UploadCard,
  DocumentsList,
  DocumentMetadataBar,
  SectionList,
  ValidationView,
  FilterConfigModal,
  ChunksView,
  type Section,
  type EpubDocument,
  type FilterConfig,
  type Chunk
} from '@/components/epub'

// Custom scrollbar and checkbox styles
const customStyles = `
  .scrollbar-custom::-webkit-scrollbar {
    width: 10px;
  }
  .scrollbar-custom::-webkit-scrollbar-track {
    background: #e5e7eb;
    border-radius: 5px;
  }
  .scrollbar-custom::-webkit-scrollbar-thumb {
    background: #9ca3af;
    border-radius: 5px;
  }
  .scrollbar-custom::-webkit-scrollbar-thumb:hover {
    background: #6b7280;
  }
  .scrollbar-custom {
    scrollbar-width: thin;
    scrollbar-color: #9ca3af #e5e7eb;
  }
  .checkbox-round {
    -webkit-appearance: none;
    -moz-appearance: none;
    appearance: none;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background-color: #f3f4f6;
    border: 2px solid #d1d5db;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s ease;
    flex-shrink: 0;
  }
  .checkbox-round:checked {
    background-color: #4f46e5;
    border-color: #4f46e5;
  }
  .checkbox-round:checked::after {
    content: '';
    width: 6px;
    height: 10px;
    border: solid white;
    border-width: 0 2px 2px 0;
    transform: rotate(45deg);
    margin-bottom: 2px;
  }
  .checkbox-round:hover {
    border-color: #9ca3af;
  }
  .checkbox-round:focus {
    outline: none;
    box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.2);
  }
  .checkbox-round:indeterminate {
    background-color: #4f46e5;
    border-color: #4f46e5;
  }
  .checkbox-round:indeterminate::after {
    content: '';
    width: 10px;
    height: 2px;
    background-color: white;
    border: none;
    transform: none;
    margin: 0;
  }
`

const EpubProcessorPage: React.FC = () => {
  const { permissions } = useAuth()
  const canEdit = permissions.epub.canEdit

  // Upload state
  const [isUploading, setIsUploading] = useState(false)
  const [epubFile, setEpubFile] = useState<File | null>(null)
  const [opfFile, setOpfFile] = useState<File | null>(null)
  
  // Documents list state
  const [documents, setDocuments] = useState<EpubDocument[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [selectedDocument, setSelectedDocument] = useState<EpubDocument | null>(null)
  
  // Section editing state
  const [editingSections, setEditingSections] = useState<Section[]>([])
  const [expandedSections, setExpandedSections] = useState<Set<number>>(new Set())
  
  // UI state
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  const [confirmDialog, setConfirmDialog] = useState<{
    isOpen: boolean
    title: string
    message: string
    onConfirm: () => void
    variant?: 'danger' | 'warning' | 'info'
  }>({ isOpen: false, title: '', message: '', onConfirm: () => {} })
  const [isExtracting, setIsExtracting] = useState(false)
  const [isApproving, setIsApproving] = useState(false)
  
  // Scanned EPUB option (enables OCR for image-based pages)
  const [enableOcr, setEnableOcr] = useState(false)
  
  // Validation view state
  const [originalSections, setOriginalSections] = useState<Section[]>([])
  const [filteredSections, setFilteredSections] = useState<Section[]>([])
  const [selectedValidateSection, setSelectedValidateSection] = useState<number | null>(null)
  const [isLoadingText, setIsLoadingText] = useState(false)
  
  // Chunks view state (for completed documents)
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [isLoadingChunks, setIsLoadingChunks] = useState(false)

  // Filter configuration state
  const [showFilterConfig, setShowFilterConfig] = useState(false)
  const [filterConfig, setFilterConfig] = useState<FilterConfig | null>(null)
  const [filterDefaults, setFilterDefaults] = useState<FilterConfig | null>(null)
  const [isSavingFilter, setIsSavingFilter] = useState(false)

  // Load documents on mount
  useEffect(() => {
    loadDocuments()
    const interval = setInterval(loadDocuments, 10000)
    return () => clearInterval(interval)
  }, [])

  // Load filter defaults on mount
  useEffect(() => {
    const loadFilterDefaults = async () => {
      try {
        const defaults = await apiService.getEpubFilterDefaults()
        setFilterDefaults(defaults)
      } catch (error) {
        console.error('Failed to load filter defaults:', error)
      }
    }
    loadFilterDefaults()
  }, [])

  const loadDocuments = useCallback(async () => {
    try {
      const response = await apiService.getEpubDocuments()
      setDocuments(response.documents || [])
    } catch (error) {
      console.error('Failed to load documents:', error)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // File selection handlers
  const handleEpubSelect = useCallback((file: File) => {
    if (!file.name.toLowerCase().endsWith('.epub')) {
      setToast({ type: 'error', message: 'Please select an EPUB file' })
      return
    }
    setEpubFile(file)
    setToast({ type: 'info', message: `EPUB selected: ${file.name}` })
  }, [])

  const handleOpfSelect = useCallback((file: File) => {
    if (!file.name.toLowerCase().endsWith('.opf')) {
      setToast({ type: 'error', message: 'Please select an OPF file (metadata.opf)' })
      return
    }
    setOpfFile(file)
    setToast({ type: 'info', message: `OPF selected: ${file.name}` })
  }, [])

  const handleFilesDropped = useCallback((files: File[]) => {
    for (const file of files) {
      const name = file.name.toLowerCase()
      if (name.endsWith('.epub')) {
        setEpubFile(file)
        setToast({ type: 'info', message: `EPUB selected: ${file.name}` })
      } else if (name.endsWith('.opf')) {
        setOpfFile(file)
        setToast({ type: 'info', message: `OPF selected: ${file.name}` })
      }
    }
  }, [])

  const handleUpload = useCallback(async (forceOverwrite: boolean = false) => {
    if (!epubFile || !opfFile) {
      setToast({ type: 'error', message: 'Please select both EPUB and OPF files' })
      return
    }
    
    setIsUploading(true)
    try {
      const response = await apiService.uploadEpub(epubFile, opfFile, forceOverwrite)
      setToast({ type: 'success', message: `Uploaded: ${response.filename}. Processing will begin shortly.` })
      loadDocuments()
      setEpubFile(null)
      setOpfFile(null)
    } catch (error) {
      // Check for duplicate error (both instanceof and name check for robustness)
      const isDuplicateError = error instanceof EpubDuplicateError || 
        (error instanceof Error && error.name === 'EpubDuplicateError')
      
      console.log('Upload error caught:', error, 'isDuplicateError:', isDuplicateError, 'error.name:', (error as Error)?.name)
      
      if (isDuplicateError) {
        // Show confirmation dialog for overwriting
        const existingDoc = (error as EpubDuplicateError).existingDocument
        const createdDate = existingDoc?.created_at 
          ? new Date(existingDoc.created_at).toLocaleDateString()
          : 'unknown date'
        
        setConfirmDialog({
          isOpen: true,
          title: 'Document Already Exists',
          message: `A document with filename "${existingDoc?.filename || epubFile.name}" already exists (uploaded on ${createdDate}, status: ${existingDoc?.status || 'unknown'}). Do you want to replace it? This will delete the existing document and its search index data.`,
          variant: 'warning',
          onConfirm: async () => {
            setConfirmDialog(prev => ({ ...prev, isOpen: false }))
            // Retry with overwrite=true
            await handleUpload(true)
          }
        })
      } else {
        console.error('Upload error:', error)
        setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to upload' })
      }
    } finally {
      setIsUploading(false)
    }
  }, [epubFile, opfFile, loadDocuments])

  // Document selection
  const selectDocument = useCallback(async (doc: EpubDocument) => {
    if (doc.status !== 'parsed' && doc.status !== 'validate' && doc.status !== 'completed') {
      setToast({ type: 'info', message: 'Document must be parsed, ready for validation, or completed' })
      return
    }
    
    try {
      const fullDoc = await apiService.getEpubDocument(doc.id)
      setSelectedDocument(fullDoc)
      setEditingSections(fullDoc.sections?.map((s: Section) => ({ ...s })) || [])
      setExpandedSections(new Set())
      
      if (fullDoc.status === 'validate') {
        setIsLoadingText(true)
        setChunks([])
        try {
          const [origSections, filtSections] = await Promise.all([
            apiService.getEpubExtractedText(doc.id, 'original'),
            apiService.getEpubExtractedText(doc.id, 'filtered')
          ])
          setOriginalSections(origSections || [])
          setFilteredSections(filtSections || [])
          
          // Flatten and auto-select first selected section (even if no content)
          const flattenAll = (sections: Section[]): Section[] => {
            const result: Section[] = []
            for (const s of sections) {
              result.push(s)
              if (s.children) result.push(...flattenAll(s.children))
            }
            return result
          }
          const flatOrig = flattenAll(origSections || [])
          // First try to find a selected section with content, then fall back to any selected section
          const firstWithContent = flatOrig.find(s => s.selected && s.content)
          const firstSelected = flatOrig.find(s => s.selected)
          if (firstWithContent) {
            setSelectedValidateSection(firstWithContent.order)
          } else if (firstSelected) {
            setSelectedValidateSection(firstSelected.order)
          }
        } catch (textError) {
          console.error('Failed to load extracted text:', textError)
          setToast({ type: 'error', message: 'Failed to load extracted text' })
        } finally {
          setIsLoadingText(false)
        }
      } else if (fullDoc.status === 'completed') {
        // Load chunks for completed documents from blob storage
        setIsLoadingChunks(true)
        setOriginalSections([])
        setFilteredSections([])
        setSelectedValidateSection(null)
        try {
          const chunksData = await apiService.getEpubChunks(doc.id)
          setChunks(chunksData || [])
        } catch (chunksError) {
          console.error('Failed to load chunks:', chunksError)
          setToast({ type: 'error', message: 'Failed to load chunks' })
          setChunks([])
        } finally {
          setIsLoadingChunks(false)
        }
      } else {
        setOriginalSections([])
        setFilteredSections([])
        setSelectedValidateSection(null)
        setChunks([])
      }
    } catch (error) {
      console.error('Failed to load document:', error)
      setToast({ type: 'error', message: 'Failed to load document details' })
    }
  }, [])

  const closeDocument = useCallback(() => {
    setSelectedDocument(null)
    setOriginalSections([])
    setFilteredSections([])
    setSelectedValidateSection(null)
    setChunks([])
  }, [])

  // Section management
  const toggleSection = useCallback((order: number) => {
    const updateSections = (sections: Section[]): Section[] => {
      return sections.map(s => {
        if (s.order === order) {
          return { ...s, selected: !s.selected }
        }
        if (s.children && s.children.length > 0) {
          return { ...s, children: updateSections(s.children) }
        }
        return s
      })
    }
    setEditingSections(prev => updateSections(prev))
  }, [])

  const toggleExpand = useCallback((order: number) => {
    setExpandedSections(prev => {
      const next = new Set(prev)
      if (next.has(order)) {
        next.delete(order)
      } else {
        next.add(order)
      }
      return next
    })
  }, [])

  const setAllSelections = useCallback((selected: boolean) => {
    const updateAll = (sections: Section[]): Section[] => {
      return sections.map(s => ({
        ...s,
        selected,
        children: s.children ? updateAll(s.children) : undefined
      }))
    }
    setEditingSections(prev => updateAll(prev))
  }, [])

  // Flatten all sections for counting
  const flattenedSections = useMemo(() => {
    const result: Section[] = []
    const flatten = (sections: Section[]) => {
      for (const section of sections) {
        result.push(section)
        if (section.children && section.children.length > 0) {
          flatten(section.children)
        }
      }
    }
    flatten(editingSections)
    return result
  }, [editingSections])

  const selectedCount = useMemo(() => {
    return flattenedSections.filter(s => s.selected).length
  }, [flattenedSections])

  // Extraction
  const startExtraction = useCallback(async () => {
    if (!selectedDocument) return
    
    const localSelectedCount = flattenedSections.filter(s => s.selected).length
    
    if (localSelectedCount === 0) {
      setToast({ type: 'error', message: 'Please select at least one section to extract' })
      return
    }
    
    const scannedMessage = enableOcr 
      ? ' This is a scanned EPUB - OCR will extract text from page images (this may take longer).'
      : ''
    
    setConfirmDialog({
      isOpen: true,
      title: 'Start Extraction',
      message: `Extract text from ${localSelectedCount} selected sections?${scannedMessage} You'll be able to review the original and filtered text before final ingestion.`,
      variant: 'info',
      onConfirm: async () => {
        setIsExtracting(true)
        try {
          const flattenForSave = (sections: Section[]): { order: number; selected: boolean }[] => {
            const result: { order: number; selected: boolean }[] = []
            for (const s of sections) {
              result.push({ order: s.order, selected: s.selected })
              if (s.children && s.children.length > 0) {
                result.push(...flattenForSave(s.children))
              }
            }
            return result
          }
          const selections = flattenForSave(editingSections)
          await apiService.updateEpubSections(selectedDocument.id, selections)
          
          await apiService.startEpubExtraction(selectedDocument.id, enableOcr)
          setToast({ type: 'success', message: `Extraction started${enableOcr ? ' (scanned EPUB with OCR)' : ''}. You can review the text once processing completes.` })
          setSelectedDocument(null)
          loadDocuments()
        } catch (error) {
          console.error('Failed to start extraction:', error)
          setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to start extraction' })
        } finally {
          setIsExtracting(false)
          setConfirmDialog(prev => ({ ...prev, isOpen: false }))
        }
      }
    })
  }, [selectedDocument, editingSections, flattenedSections, loadDocuments, enableOcr])

  // Approval
  const approveDocument = useCallback(async () => {
    if (!selectedDocument) return
    
    setConfirmDialog({
      isOpen: true,
      title: 'Approve & Ingest',
      message: `Approve this document and start ingestion? The filtered text will be indexed for search.`,
      variant: 'info',
      onConfirm: async () => {
        setIsApproving(true)
        try {
          await apiService.approveEpubDocument(selectedDocument.id)
          setToast({ type: 'success', message: 'Document approved. Ingestion started.' })
          closeDocument()
          loadDocuments()
        } catch (error) {
          console.error('Failed to approve document:', error)
          setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to approve' })
        } finally {
          setIsApproving(false)
          setConfirmDialog(prev => ({ ...prev, isOpen: false }))
        }
      }
    })
  }, [selectedDocument, closeDocument, loadDocuments])

  // Document actions
  const deleteDocument = useCallback(async (docId: string, filename: string) => {
    setConfirmDialog({
      isOpen: true,
      title: 'Delete Document',
      message: `Are you sure you want to delete "${filename}"? This action cannot be undone.`,
      variant: 'danger',
      onConfirm: async () => {
        try {
          await apiService.deleteEpubDocument(docId)
          setToast({ type: 'success', message: 'Document deleted' })
          if (selectedDocument?.id === docId) {
            setSelectedDocument(null)
          }
          loadDocuments()
        } catch (error) {
          console.error('Failed to delete document:', error)
          setToast({ type: 'error', message: 'Failed to delete document' })
        }
        setConfirmDialog(prev => ({ ...prev, isOpen: false }))
      }
    })
  }, [selectedDocument, loadDocuments])

  const retryDocument = useCallback(async (docId: string, filename: string) => {
    setConfirmDialog({
      isOpen: true,
      title: 'Retry Processing',
      message: `Retry processing for "${filename}"? This will restart from the last successful step.`,
      variant: 'warning',
      onConfirm: async () => {
        try {
          const result = await apiService.retryEpubDocument(docId)
          setToast({ type: 'success', message: result.message || 'Retry started' })
          loadDocuments()
        } catch (error) {
          console.error('Failed to retry document:', error)
          setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to retry' })
        }
        setConfirmDialog(prev => ({ ...prev, isOpen: false }))
      }
    })
  }, [loadDocuments])

  // Filter configuration
  const openFilterConfig = useCallback(async () => {
    if (!selectedDocument) return
    
    try {
      const { filter_config } = await apiService.getEpubFilterConfig(selectedDocument.id)
      if (filter_config) {
        setFilterConfig(filter_config)
      } else if (filterDefaults) {
        setFilterConfig({ ...filterDefaults })
      }
      setShowFilterConfig(true)
    } catch (error) {
      console.error('Failed to load filter config:', error)
      setToast({ type: 'error', message: 'Failed to load filter configuration' })
    }
  }, [selectedDocument, filterDefaults])

  const saveFilterConfig = useCallback(async () => {
    if (!selectedDocument || !filterConfig) return
    
    setIsSavingFilter(true)
    try {
      await apiService.updateEpubFilterConfig(selectedDocument.id, filterConfig)
      setToast({ type: 'success', message: 'Filter configuration saved' })
      setShowFilterConfig(false)
    } catch (error) {
      console.error('Failed to save filter config:', error)
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to save' })
    } finally {
      setIsSavingFilter(false)
    }
  }, [selectedDocument, filterConfig])

  const saveAndReExtract = useCallback(async () => {
    if (!selectedDocument || !filterConfig) return
    
    setIsSavingFilter(true)
    try {
      await apiService.updateEpubFilterConfig(selectedDocument.id, filterConfig)
      await apiService.reExtractEpubDocument(selectedDocument.id)
      setToast({ type: 'success', message: 'Filters saved. Re-extracting content...' })
      setShowFilterConfig(false)
      closeDocument()
      loadDocuments()
    } catch (error) {
      console.error('Failed to re-extract:', error)
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Failed to re-extract' })
    } finally {
      setIsSavingFilter(false)
    }
  }, [selectedDocument, filterConfig, closeDocument, loadDocuments])

  const resetFilterToDefaults = useCallback(() => {
    if (filterDefaults) {
      setFilterConfig({ ...filterDefaults })
    }
  }, [filterDefaults])

  return (
    <div className="bg-gray-50">
      <style>{customStyles}</style>
      
      <PageHeader
        title="EPUB Processor"
        subtitle="Upload, parse, and ingest EPUB content"
      />

      {/* Admin-only notice */}
      {!canEdit && (
        <div className="px-6 pt-3">
          <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
            <Lock className="w-4 h-4 flex-shrink-0" />
            <span>Admin access required to upload and process EPUB files. You have read-only access.</span>
          </div>
        </div>
      )}

      <PipelineStats documents={documents} onRefresh={loadDocuments} />

      {/* Main Content */}
      <div className="px-6 py-3">
        <div className="flex gap-4 h-[calc(100vh-180px)]">
          {/* Left Sidebar - Upload & Documents */}
          <div className="w-72 flex-shrink-0 flex flex-col gap-3">
            <UploadCard
              epubFile={epubFile}
              opfFile={opfFile}
              isUploading={isUploading}
              disabled={!canEdit}
              onEpubSelect={handleEpubSelect}
              onOpfSelect={handleOpfSelect}
              onUpload={() => handleUpload(false)}
              onFilesDropped={handleFilesDropped}
            />

            <DocumentsList
              documents={documents}
              selectedDocumentId={selectedDocument?.id || null}
              isLoading={isLoading}
              canEdit={canEdit}
              onSelectDocument={selectDocument}
              onDeleteDocument={deleteDocument}
              onRetryDocument={retryDocument}
            />
          </div>

          {/* Main Content Area */}
          <div className="flex-1 flex flex-col min-h-0 gap-3">
            {selectedDocument ? (
              <>
                <DocumentMetadataBar
                  document={selectedDocument}
                  onClose={closeDocument}
                  onOpenFilters={openFilterConfig}
                  showFilterButton={selectedDocument.status === 'parsed' || selectedDocument.status === 'validate'}
                  showChunksInfo={selectedDocument.status === 'completed'}
                />

                {/* Conditional View based on status */}
                {selectedDocument.status === 'completed' ? (
                  <ChunksView
                    chunks={chunks}
                    isLoading={isLoadingChunks}
                  />
                ) : selectedDocument.status === 'validate' ? (
                  <ValidationView
                    originalSections={originalSections}
                    filteredSections={filteredSections}
                    selectedSectionOrder={selectedValidateSection}
                    isLoading={isLoadingText}
                    filterConfig={filterConfig}
                    filterDefaults={filterDefaults}
                    onSelectSection={setSelectedValidateSection}
                  />
                ) : (
                  <SectionList
                    sections={editingSections}
                    expandedSections={expandedSections}
                    onToggleSection={toggleSection}
                    onToggleExpand={toggleExpand}
                    onSelectAll={() => setAllSelections(true)}
                    onDeselectAll={() => setAllSelections(false)}
                  />
                )}

                {/* Action Button */}
                <div className="flex-shrink-0 space-y-2">
                  {selectedDocument.status === 'completed' ? (
                    <div className="py-2.5 bg-green-50 border border-green-200 text-green-700 rounded-lg font-medium flex items-center justify-center gap-2">
                      <CheckCircle2 className="w-4 h-4" />
                      Document Ingested ({selectedDocument.ingested_chunks || chunks.length} chunks)
                    </div>
                  ) : selectedDocument.status === 'validate' ? (
                    <button
                      onClick={approveDocument}
                      disabled={isApproving || !canEdit}
                      title={!canEdit ? 'Admin access required' : undefined}
                      className="w-full py-2.5 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                    >
                      {isApproving ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Approving...
                        </>
                      ) : (
                        <>
                          {!canEdit && <Lock className="w-4 h-4" />}
                          <CheckCircle2 className="w-4 h-4" />
                          Approve & Ingest
                        </>
                      )}
                    </button>
                  ) : (
                    <>
                      {/* Scanned EPUB Option */}
                      <label className={`flex items-center gap-3 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg transition-colors ${canEdit ? 'cursor-pointer hover:bg-amber-100' : 'cursor-not-allowed opacity-60'}`}>
                        <div className="relative flex items-center justify-center">
                          <input
                            type="checkbox"
                            checked={enableOcr}
                            onChange={(e) => setEnableOcr(e.target.checked)}
                            disabled={!canEdit}
                            className="peer sr-only"
                          />
                          <div className="w-5 h-5 bg-white border-2 border-gray-300 rounded peer-checked:bg-amber-600 peer-checked:border-amber-600 peer-focus:ring-2 peer-focus:ring-amber-500 peer-focus:ring-offset-1 transition-colors" />
                          <svg 
                            className="absolute w-3 h-3 text-white opacity-0 peer-checked:opacity-100 pointer-events-none transition-opacity" 
                            fill="none" 
                            stroke="currentColor" 
                            viewBox="0 0 24 24"
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                          </svg>
                        </div>
                        <div className="flex-1">
                          <span className="text-sm font-medium text-amber-800">Is scanned EPUB file?</span>
                          <p className="text-xs text-amber-600">Uses OCR to extract text from page images (slower, additional cost)</p>
                        </div>
                      </label>
                      
                      <button
                        onClick={startExtraction}
                        disabled={selectedCount === 0 || isExtracting || !canEdit}
                        title={!canEdit ? 'Admin access required' : undefined}
                        className="w-full py-2.5 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                      >
                        {isExtracting ? (
                          <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            Starting...
                          </>
                        ) : (
                          <>
                            {!canEdit && <Lock className="w-4 h-4" />}
                            <Play className="w-4 h-4" />
                            Extract Selected ({selectedCount}){enableOcr && ' (Scanned)'}
                          </>
                        )}
                      </button>
                    </>
                  )}
                </div>
              </>
            ) : (
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 flex-1 flex items-center justify-center">
                <div className="text-center">
                  <Eye className="w-12 h-12 text-gray-300 mx-auto mb-3" />
                  <h3 className="font-semibold text-gray-900 mb-1">Select a Document</h3>
                  <p className="text-sm text-gray-500">Click on a parsed document to view sections</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={() => setToast(null)}
          />
        </div>
      )}

      {/* Confirm Dialog */}
      <ConfirmDialog
        isOpen={confirmDialog.isOpen}
        onClose={() => setConfirmDialog(prev => ({ ...prev, isOpen: false }))}
        onConfirm={confirmDialog.onConfirm}
        title={confirmDialog.title}
        message={confirmDialog.message}
        variant={confirmDialog.variant}
        isLoading={isExtracting || isApproving}
      />

      {/* Filter Configuration Modal */}
      {filterConfig && (
        <FilterConfigModal
          isOpen={showFilterConfig}
          filterConfig={filterConfig}
          filterDefaults={filterDefaults}
          isSaving={isSavingFilter}
          isValidateMode={selectedDocument?.status === 'validate'}
          onClose={() => setShowFilterConfig(false)}
          onChange={setFilterConfig}
          onSave={saveFilterConfig}
          onSaveAndReExtract={saveAndReExtract}
          onResetToDefaults={resetFilterToDefaults}
        />
      )}
    </div>
  )
}

export default EpubProcessorPage
