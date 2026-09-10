'use client'

import React, { useState, useEffect, useImperativeHandle, forwardRef } from 'react'
import { UserCheck, AlertCircle, Save, Loader2, Sparkles } from 'lucide-react'
import OCRTabs from './OCRTabs'
import VisualTabs from './VisualTabs'
import { apiService, ApiDocumentMetadata, AssetDetail } from '@/services/api'

interface CorrectionsPanelProps {
  transcription: string
  onTranscriptionChange: (transcription: string) => void
  modifiedText: string
  onModifiedTextChange: (text: string) => void
  title: string
  onTitleChange: (value: string) => void
  onTitleBlur?: (value: string) => void
  description: string
  onDescriptionChange: (value: string) => void
  onDescriptionBlur?: (value: string) => void
  creationDate: string
  onCreationDateChange: (value: string) => void
  onCreationDateBlur?: (value: string) => void
  creator: string
  onCreatorChange: (value: string) => void
  onCreatorBlur?: (value: string) => void
  recipient: string
  onRecipientChange: (value: string) => void
  onRecipientBlur?: (value: string) => void
  citation: string
  onCitationChange: (value: string) => void
  onCitationBlur?: (value: string) => void
  resourceType: string
  onResourceTypeChange: (value: string) => void
  onResourceTypeBlur?: (value: string) => void
  period: string
  onPeriodChange: (value: string) => void
  onPeriodBlur?: (value: string) => void
  repository: string
  onRepositoryChange: (value: string) => void
  rights: string
  onRightsChange: (value: string) => void
  onRightsBlur?: (value: string) => void
  productionMethod: string
  onProductionMethodChange: (value: string) => void
  onProductionMethodBlur?: (value: string) => void
  language: string
  onLanguageChange: (value: string) => void
  onLanguageBlur?: (value: string) => void
  severeDeviation: boolean
  onSevereDeviationChange: (deviation: boolean) => void
  deviationNotes: string
  onDeviationNotesChange: (notes: string) => void
  archivistNotes: string
  onArchivistNotesChange: (notes: string) => void
  onArchivistNotesBlur?: (notes: string) => void
  markComplete: boolean
  onMarkCompleteChange: (complete: boolean) => void
  onFormChange: () => void
  onModifiedTextBlur?: (text: string) => void
  documentId: string
  isPublishing?: boolean
  onApproveForPublishing?: () => void
  onUndoPublishing?: () => void
  compareOriginalText?: string
  compareModifiedText?: string
  dirtyFields?: Set<string>
  apiDocument?: ApiDocumentMetadata | null | undefined
  currentAsset?: AssetDetail
  onSaveAll?: () => void
  isSaving?: boolean
  unsavedCount?: number
  isPublished?: boolean
  // Visual description props
  visualDescriptionModified?: string
  onVisualDescriptionChange?: (text: string) => void
  onVisualDescriptionBlur?: (text: string) => void
  // Read-only mode - disables all editing (for users without edit permission)
  readOnly?: boolean
}

export interface CorrectionsPanelRef {
  refreshLastSaved: () => Promise<void>
}

interface MetadataFieldMapping {
  name: string
  label: string
  metadataKeys: string[]
  extractedKeys: string[]
  currentValue: string
  onChange: (value: string) => void
  onBlur?: (value: string) => void
  placeholder?: string
  isTextarea?: boolean
  rows?: number
  isRequired?: boolean
}

interface SelectedMetadata {
  label: string
  originalValue: string
  extractedValue: string
  currentValue: string
  onChange: (value: string) => void
  onBlur?: (value: string) => void
  isTextarea?: boolean
  rows?: number
}

const CorrectionsPanel = forwardRef<CorrectionsPanelRef, CorrectionsPanelProps>(({
  transcription,
  onTranscriptionChange,
  modifiedText,
  onModifiedTextChange,
  title,
  onTitleChange,
  onTitleBlur,
  description,
  onDescriptionChange,
  onDescriptionBlur,
  creationDate,
  onCreationDateChange,
  onCreationDateBlur,
  creator,
  onCreatorChange,
  onCreatorBlur,
  recipient,
  onRecipientChange,
  onRecipientBlur,
  citation,
  onCitationChange,
  onCitationBlur,
  resourceType,
  onResourceTypeChange,
  onResourceTypeBlur,
  period,
  onPeriodChange,
  onPeriodBlur,
  repository,
  onRepositoryChange,
  rights,
  onRightsChange,
  onRightsBlur,
  productionMethod,
  onProductionMethodChange,
  onProductionMethodBlur,
  language,
  onLanguageChange,
  onLanguageBlur,
  severeDeviation,
  onSevereDeviationChange,
  deviationNotes,
  onDeviationNotesChange,
  archivistNotes,
  onArchivistNotesChange,
  onArchivistNotesBlur,
  markComplete,
  onMarkCompleteChange,
  onFormChange,
  onModifiedTextBlur,
  documentId,
  isPublishing = false,
  onApproveForPublishing,
  onUndoPublishing,
  compareOriginalText,
  compareModifiedText,
  dirtyFields = new Set(),
  apiDocument,
  currentAsset,
  onSaveAll,
  isSaving = false,
  unsavedCount = 0,
  isPublished = false,
  visualDescriptionModified,
  onVisualDescriptionChange,
  onVisualDescriptionBlur,
  readOnly = false,
}, ref) => {
  const [lastSaved, setLastSaved] = useState<Date | null>(null)
  const [selectedMetadata, setSelectedMetadata] = useState<SelectedMetadata | null>(null)
  const [useExtractedMetadata, setUseExtractedMetadata] = useState(false) // Toggle between metadata and extracted_metadata

  // Fetch the most recent change from audit history
  const fetchMostRecentChange = async () => {
    if (!documentId) return
    
    try {
      const response = await apiService.getAuditHistory(documentId, 1) // Only fetch the most recent entry
      if (response.entries && response.entries.length > 0) {
        const mostRecentEntry = response.entries[0]
        setLastSaved(new Date(mostRecentEntry.ts))
      } else {
        setLastSaved(null)
      }
    } catch (err) {
      console.error('Failed to fetch most recent change:', err)
      setLastSaved(null)
    }
  }

  useEffect(() => {
    fetchMostRecentChange()
  }, [documentId])

  // Expose refresh method to parent component
  useImperativeHandle(ref, () => ({
    refreshLastSaved: fetchMostRecentChange
  }))

  const formatLastSaved = (date: Date | null) => {
    if (!date) return 'No recent changes'
    const formattedDate = date.toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
    })
    const formattedTime = date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    })
    return `on ${formattedDate} at ${formattedTime}`
  }

  // Helper to get field class based on dirty state
  const getFieldClass = (fieldName: string, baseClass: string) => {
    const isDirty = dirtyFields.has(fieldName)
    return `${baseClass} ${isDirty ? 'border-l-2 border-amber-300 bg-amber-50/30' : ''}`
  }

  // Helper function to extract value from metadata or extracted_metadata
  const extractMetadataValue = (keys: string[], source?: { [key: string]: any }): string => {
    if (!source) return ''
    
    // First try exact key matches
    for (const key of keys) {
      const value = source[key]
      if (value !== undefined && value !== null && value !== '') {
        if (typeof value === 'string') {
          const trimmed = value.trim()
          if (trimmed) return trimmed
        } else if (typeof value === 'object') {
          if (value.label) return String(value.label).trim()
          if (value.name) return String(value.name).trim()
          if (value.title) return String(value.title).trim()
          if (value.text) return String(value.text).trim()
        } else {
          const strValue = String(value).trim()
          if (strValue) return strValue
        }
      }
    }
    
    // If no exact match, try case-insensitive matching
    const sourceKeys = Object.keys(source)
    for (const key of keys) {
      const lowerKey = key.toLowerCase()
      for (const sourceKey of sourceKeys) {
        if (sourceKey.toLowerCase() === lowerKey) {
          const value = source[sourceKey]
          if (value !== undefined && value !== null && value !== '') {
            if (typeof value === 'string') {
              const trimmed = value.trim()
              if (trimmed) return trimmed
            } else if (typeof value === 'object') {
              if (value.label) return String(value.label).trim()
              if (value.name) return String(value.name).trim()
              if (value.title) return String(value.title).trim()
              if (value.text) return String(value.text).trim()
            } else {
              const strValue = String(value).trim()
              if (strValue) return strValue
            }
          }
        }
      }
    }
    
    return ''
  }

  // Helper to parse HTML descriptions
  const parseHtmlDescription = (html: string): string => {
    if (!html) return ''
    
    if (html.trim().startsWith('<')) {
      const parser = new DOMParser()
      const doc = parser.parseFromString(html, 'text/html')
      const content = doc.body?.textContent || doc.documentElement?.textContent || ''
      return content.trim().replace(/\s+/g, ' ')
    }
    
    return html
  }

  // Truncate text for table display
  const truncateText = (text: string, maxLength: number = 50) => {
    if (!text) return '(empty)'
    const str = String(text)
    return str.length <= maxLength ? str : str.substring(0, maxLength) + '...'
  }

  // Define metadata field mappings
  const metadataFields: MetadataFieldMapping[] = [
    {
      name: 'title',
      label: 'Title',
      metadataKeys: ['Title'],
      extractedKeys: ['Title', 'title'], // Support both capitalized and lowercase
      currentValue: title,
      onChange: onTitleChange,
      onBlur: onTitleBlur,
      placeholder: 'Enter document title...',
      isRequired: true
    },
    {
      name: 'description',
      label: 'Description',
      metadataKeys: ['Description', 'Summary', 'Abstract'],
      extractedKeys: ['Description', 'description', 'summary', 'subject'], // Support both capitalized and lowercase
      currentValue: description,
      onChange: onDescriptionChange,
      onBlur: onDescriptionBlur,
      placeholder: 'Enter document description...',
      isTextarea: true,
      rows: 3
    },
    {
      name: 'creator',
      label: 'Creator',
      metadataKeys: ['Creator', 'Author', 'Writer'],
      extractedKeys: ['Creator', 'sender', 'creator', 'author'], // Support both capitalized and lowercase
      currentValue: creator,
      onChange: onCreatorChange,
      onBlur: onCreatorBlur,
      placeholder: 'e.g., Theodore Roosevelt'
    },
    {
      name: 'recipient',
      label: 'Recipient',
      metadataKeys: ['Recipient', 'Addressee', 'To'],
      extractedKeys: ['Recipient', 'recipient', 'addressee'], // Support both capitalized and lowercase
      currentValue: recipient,
      onChange: onRecipientChange,
      onBlur: onRecipientBlur,
      placeholder: 'e.g., John Smith'
    },
    {
      name: 'creationDate',
      label: 'Creation Date',
      metadataKeys: ['Creation Date', 'Date Created', 'Date', 'Issue Date'],
      extractedKeys: ['Creation Date', 'date', 'created_date'], // Support both capitalized and lowercase
      currentValue: creationDate,
      onChange: onCreationDateChange,
      onBlur: onCreationDateBlur,
      placeholder: 'e.g., 1901-09-14 or September 1901'
    },
    {
      name: 'resourceType',
      label: 'Resource Type',
      metadataKeys: ['Resource Type', 'Type', 'Document Type'],
      extractedKeys: ['Resource Type', 'resource_type', 'document_type', 'type'], // Support both capitalized and lowercase
      currentValue: resourceType,
      onChange: onResourceTypeChange,
      onBlur: onResourceTypeBlur,
      placeholder: 'e.g., Letter, Photograph'
    },
    {
      name: 'period',
      label: 'Period',
      metadataKeys: ['Period', 'Era', 'Time Period'],
      extractedKeys: ['Period', 'period', 'era'], // Support both capitalized and lowercase
      currentValue: period,
      onChange: onPeriodChange,
      onBlur: onPeriodBlur,
      placeholder: 'e.g., Progressive Era, Presidency'
    },
    {
      name: 'productionMethod',
      label: 'Production Method',
      metadataKeys: ['Production Method', 'Method', 'Format'],
      extractedKeys: ['Production Method', 'production_method', 'method'], // Support both capitalized and lowercase
      currentValue: productionMethod,
      onChange: onProductionMethodChange,
      onBlur: onProductionMethodBlur,
      placeholder: 'e.g., Typed, Handwritten'
    },
    {
      name: 'citation',
      label: 'Citation',
      metadataKeys: ['Citation', 'Source Citation', 'Reference'],
      extractedKeys: ['Citation', 'citation'], // Support both capitalized and lowercase
      currentValue: citation,
      onChange: onCitationChange,
      onBlur: onCitationBlur,
      placeholder: 'Enter citation information...',
      isTextarea: true,
      rows: 2
    },
    {
      name: 'rights',
      label: 'Rights',
      metadataKeys: ['Copyright Status', 'Image Rights', 'Rights', 'Copyright'],
      extractedKeys: ['Rights', 'rights', 'copyright'], // Support both capitalized and lowercase
      currentValue: rights,
      onChange: onRightsChange,
      onBlur: onRightsBlur,
      placeholder: 'e.g., Public Domain'
    },
    {
      name: 'language',
      label: 'Language',
      metadataKeys: ['Language', 'Languages'],
      extractedKeys: ['Language', 'language', 'languages'], // Support both capitalized and lowercase
      currentValue: language,
      onChange: onLanguageChange,
      onBlur: onLanguageBlur,
      placeholder: 'e.g., English'
    }
  ]

  // Get original and extracted values for each field
  const getFieldMetadata = (field: MetadataFieldMapping) => {
    let originalValue = extractMetadataValue(field.metadataKeys, apiDocument?.metadata)
    let extractedValue = extractMetadataValue(field.extractedKeys, apiDocument?.extracted_metadata)

    // Special handling for description HTML parsing
    if (field.label === 'Description') {
      if (originalValue) originalValue = parseHtmlDescription(originalValue)
      if (extractedValue) extractedValue = parseHtmlDescription(extractedValue)
    }

    return {
      originalValue,
      extractedValue
    }
  }

  return (
    <section className="bg-white flex flex-col h-full">
      {/* Panel Header with Unified Status */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-museum-200 bg-slate-100">
        <div className="flex items-center space-x-3">
          <h3 className="text-base font-semibold text-museum-900">Human Validation & Corrections</h3>
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-200 text-slate-800">
            <UserCheck className="w-3 h-3 mr-1" />
            Archivist Review
          </span>
        </div>
        
        {/* Save All Button and Status */}
        <div className="flex items-center gap-3">
          {/* Save All Button */}
          {onSaveAll && (
            <button
              onClick={onSaveAll}
              disabled={isSaving || unsavedCount === 0}
              className="flex items-center gap-2 px-4 py-2 bg-museum-600 text-white rounded-lg 
                         hover:bg-museum-700 transition-all disabled:opacity-40 disabled:cursor-not-allowed
                         shadow-sm hover:shadow-md text-sm font-medium relative"
              title={
                unsavedCount > 0 
                  ? `Save ${unsavedCount} unsaved ${unsavedCount === 1 ? 'change' : 'changes'}` 
                  : 'No changes to save'
              }
            >
              {isSaving ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Saving...</span>
                </>
              ) : (
                <>
                  <Save className="w-4 h-4" />
                  <span>Save All</span>
                  {unsavedCount > 0 && (
                    <span className="absolute -top-1 -right-1 bg-amber-500 text-white text-xs font-bold 
                                     w-5 h-5 rounded-full flex items-center justify-center">
                      {unsavedCount}
                    </span>
                  )}
                </>
              )}
            </button>
          )}
          
          {/* Unified Status - Shows save state and time */}
          <div className="flex items-center gap-2 text-sm">
          {dirtyFields.size > 0 ? (
            <>
              <div className="w-2 h-2 rounded-full bg-amber-500 animate-pulse"></div>
              <span className="font-medium text-amber-900">
                {dirtyFields.size} unsaved
              </span>
            </>
          ) : (
            <>
              <div className="w-2 h-2 rounded-full bg-green-600"></div>
              <span className="font-medium text-green-800">Saved</span>
              <span className="text-museum-500">
                {lastSaved ? formatLastSaved(lastSaved).replace('on ', '').replace(' at', ',') : ''}
              </span>
            </>
          )}
          </div>
        </div>
      </div>

      {/* Scrollable Content */}
      <div className="flex-1 overflow-y-auto ocr-scrollbar">
        <div className="p-4 space-y-6">
          {/* Visual Tabs or OCR Tabs - Conditional based on visual_description_possible */}
          {apiDocument?.visual_description_possible === "Y" ? (
            <VisualTabs
              originalText={apiDocument.visual_detailed_description_original || ""}
              modifiedText={visualDescriptionModified !== undefined ? visualDescriptionModified : (apiDocument.visual_detailed_description_flexible || "")}
              onModifiedTextChange={(text: string) => {
                if (onVisualDescriptionChange) {
                  onVisualDescriptionChange(text)
                } else {
                  // Fallback to onModifiedTextChange if onVisualDescriptionChange not provided
                  onModifiedTextChange(text)
                }
                onFormChange()
              }}
              onFormChange={onFormChange}
              onModifiedTextBlur={(text: string) => {
                if (onVisualDescriptionBlur) {
                  onVisualDescriptionBlur(text)
                } else if (onModifiedTextBlur) {
                  onModifiedTextBlur(text)
                }
              }}
              compareOriginalText={apiDocument.visual_detailed_description_original}
              compareModifiedText={visualDescriptionModified !== undefined ? visualDescriptionModified : (apiDocument.visual_detailed_description_flexible || "")}
              documentData={apiDocument}
            />
          ) : (
            <OCRTabs
              originalText={transcription}
              modifiedText={modifiedText}
              onModifiedTextChange={onModifiedTextChange}
              onFormChange={onFormChange}
              onModifiedTextBlur={onModifiedTextBlur}
              compareOriginalText={compareOriginalText}
              compareModifiedText={compareModifiedText}
              currentAsset={currentAsset}
              documentData={apiDocument}
            />
          )}

          {/* Document Metadata */}
          <div className="space-y-4 border-t border-museum-200 pt-4">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-sm font-semibold text-museum-900">Document Metadata</h4>
              
              {/* Toggle Switch */}
              <div className="flex items-center gap-2">
                <span className={`text-xs font-medium ${!useExtractedMetadata ? 'text-museum-900' : 'text-museum-500'}`}>
                  Metadata
                </span>
                <button
                  onClick={() => setUseExtractedMetadata(!useExtractedMetadata)}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-museum-500 focus:ring-offset-2 ${
                    useExtractedMetadata ? 'bg-museum-accent' : 'bg-museum-300'
                  }`}
                  role="switch"
                  aria-checked={useExtractedMetadata}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      useExtractedMetadata ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
                <span className={`text-xs font-medium ${useExtractedMetadata ? 'text-museum-900' : 'text-museum-500'}`}>
                  Extracted Metadata
                </span>
              </div>
            </div>

            {/* Metadata Confidence - Only show when Extracted Metadata toggle is ON */}
            {useExtractedMetadata && apiDocument && (
              <div className="mb-3 pb-3 border-b border-museum-200">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-museum-accent" />
                  <span className="text-sm text-gray-600">Metadata Confidence:</span>
                  {(() => {
                    const metaConf = apiDocument.metadata_extraction_confidence ?? 0;
                    const percentage = Math.round(metaConf * 100);
                    const color = percentage >= 90 ? 'text-green-600' : 
                                  percentage >= 70 ? 'text-yellow-600' : 
                                  percentage >= 50 ? 'text-orange-600' : 'text-red-600';
                    // const bgColor = percentage >= 90 ? 'bg-green-500' : 
                    //                 percentage >= 70 ? 'bg-yellow-500' : 
                    //                 percentage >= 50 ? 'bg-orange-500' : 'bg-red-500';
                    return (
                      <div className="flex items-center gap-2">
                        {/* <div className="w-20 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                          <div className={`h-full ${bgColor} rounded-full transition-all`} style={{ width: `${percentage}%` }} />
                        </div> */}
                        <span className={`font-medium text-sm ${color}`}>{percentage}%</span>
                      </div>
                    );
                  })()}
                </div>
              </div>
            )}

            {/* Metadata Form - Two Columns */}
            <div className="grid grid-cols-2 gap-4">
              {metadataFields.map((field) => {
                const { originalValue, extractedValue } = getFieldMetadata(field)
                
                // Fields that should span two columns
                const fullWidthFields = ['title', 'description', 'citation']
                const shouldSpanFullWidth = fullWidthFields.includes(field.name)
                
                return (
                  <div key={field.name} className={shouldSpanFullWidth ? 'col-span-2' : ''}>
                    <label className="block text-sm font-medium text-museum-700 mb-1">
                      {field.label}
                      {field.isRequired && <span className="text-red-500 ml-1">*</span>}
                    </label>
                    
                    {/* Extracted Metadata Display - Always show on top when toggle is ON */}
                    {useExtractedMetadata && extractedValue && (
                      <div className="mb-2 text-xs text-museum-600 bg-museum-50 px-2 py-1 rounded border border-museum-accent">
                        <div className="flex items-center gap-1.5">
                          <Sparkles className="w-3.5 h-3.5 text-museum-accent" />
                          {field.isTextarea ? (
                            <div className="mt-1 whitespace-pre-wrap flex-1">{extractedValue}</div>
                          ) : (
                            <span className="flex-1">{extractedValue}</span>
                          )}
                        </div>
                      </div>
                    )}
                    
                    {/* Editable Input */}
                    {field.isTextarea ? (
                      <textarea
                        className={getFieldClass(field.name, 'w-full px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm transition-colors resize-none')}
                        rows={field.rows || 3}
                        placeholder={field.placeholder}
                        value={field.currentValue}
                        onChange={(e) => {
                          field.onChange(e.target.value)
                          onFormChange()
                        }}
                        onBlur={(e) => field.onBlur?.(e.target.value)}
                      />
                    ) : (
                      <input
                        type="text"
                        className={getFieldClass(field.name, 'w-full px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm transition-colors')}
                        placeholder={field.placeholder}
                        value={field.currentValue}
                        onChange={(e) => {
                          field.onChange(e.target.value)
                          onFormChange()
                        }}
                        onBlur={(e) => field.onBlur?.(e.target.value)}
                      />
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Review Section */}
          <div className="border-t border-museum-200 pt-4">
            <h4 className="text-sm font-semibold text-museum-900 mb-3">Review Status & Flags</h4>

            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-museum-700 mb-1">Archivist Notes</label>
                <textarea
                  className={getFieldClass('archivistNotes', 'w-full px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm transition-colors')}
                  rows={3}
                  placeholder="Additional notes about this document..."
                  value={archivistNotes}
                  onChange={(e) => {
                    onArchivistNotesChange(e.target.value)
                    onFormChange()
                  }}
                  onBlur={(e) => onArchivistNotesBlur?.(e.target.value)}
                />
              </div>

              {/* Completion */}
              <div className={`border rounded-lg p-3 ${isPublishing || isPublished || readOnly ? 'bg-gray-50 border-gray-300' : 'bg-green-50 border-green-200'}`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="mark-complete"
                      className="w-4 h-4 text-green-600 border-green-300 rounded focus:ring-green-500 disabled:opacity-50 disabled:cursor-not-allowed"
                      checked={markComplete}
                      disabled={isPublishing || isPublished || readOnly}
                      onChange={(e) => {
                        onMarkCompleteChange(e.target.checked)
                        onFormChange()
                      }}
                    />
                    <label 
                      htmlFor="mark-complete" 
                      className={`text-sm font-medium ${isPublishing || isPublished || readOnly ? 'text-gray-600' : 'text-green-900'}`}
                    >
                      Mark as Reviewed
                    </label>
                  </div>
                  <span className={`text-xs font-medium ${isPublishing || isPublished || readOnly ? 'text-gray-500' : 'text-green-600'}`}>
                    Ready for publication
                  </span>
                </div>
                <p className={`text-sm mt-1 ${isPublishing || isPublished || readOnly ? 'text-gray-600' : 'text-green-700'}`}>
                  {readOnly
                    ? 'Read-only access. You cannot modify review status.'
                    : isPublished
                    ? 'Document is published. Modifications will change status to pending.'
                    : isPublishing 
                    ? 'Document is approved for publishing. Uncheck "Approve for Publishing" to modify review status.'
                    : 'All required fields validated. Document ready for public access.'}
                </p>
              </div>
              {!isPublishing && !isPublished ? (
                <div className="relative">
                <button
                  onClick={onApproveForPublishing}
                  disabled={!markComplete || readOnly}
                  className={`w-full inline-flex items-center justify-center px-4 py-3 rounded-lg text-sm font-medium transition-all ${
                    markComplete && !readOnly
                      ? 'bg-purple-600 text-white hover:bg-purple-700 shadow-sm hover:shadow-md'
                      : 'bg-gray-200 text-gray-400 cursor-not-allowed'
                  }`}
                  title={readOnly ? 'Read-only access' : !markComplete ? 'The document must be marked as reviewed' : 'Approve for publishing'}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Approve for Publishing
                </button>
                  {!markComplete && (
                    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                      <span className="bg-gray-800 text-white text-xs px-3 py-1 rounded shadow-lg opacity-0 hover:opacity-100 transition-opacity">
                        The document must be marked as reviewed
                      </span>
                    </div>
                  )}
                </div>
              ) : isPublished ? (
                <div className="bg-green-50 border border-green-200 rounded-lg p-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center space-x-2">
                      <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <span className="text-sm font-semibold text-green-900">Published</span>
                    </div>
                    <span className="text-xs text-green-600 font-medium">Public Access</span>
                  </div>
                  <p className="text-sm text-green-700">
                    This document is published. Any modifications will change its status to pending for review.
                  </p>
                </div>
              ) : (
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center space-x-2">
                      <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-purple-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <span className="text-sm font-semibold text-purple-900">Publishing Approved</span>
                    </div>
                    <span className="text-xs text-purple-600 font-medium">Pending Publication</span>
                  </div>
                  <p className="text-sm text-purple-700 mb-3">
                    This document has been approved and is queued for publishing.
                  </p>
                  <button
                    onClick={onUndoPublishing}
                    disabled={readOnly}
                    className={`w-full inline-flex items-center justify-center px-4 py-2 rounded-lg text-sm font-medium transition-colors shadow-sm hover:shadow-md ${
                      readOnly
                        ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                        : 'bg-orange-600 text-white hover:bg-orange-700'
                    }`}
                    title={readOnly ? 'Read-only access' : 'Undo publishing approval'}
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                    Undo Publishing Approval
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Metadata Comparison Modal */}
      {selectedMetadata && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50"
          onClick={() => setSelectedMetadata(null)}
        >
          <div
            className="bg-white rounded-lg shadow-xl max-w-6xl w-full mx-4 max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-6 py-4 border-b border-museum-200 bg-museum-50 flex justify-center items-center">
                <h3 className="text-lg  text-center font-semibold text-museum-900">Metadata Comparison: {selectedMetadata.label}</h3>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              {/* Current Editable Value */}
              <div className="mb-4">
                <h4 className="text-sm font-semibold text-museum-700 mb-2 flex items-center gap-2">
                  Current Value (Editable)
                  </h4>
                <div className="bg-yellow-50 border-2 border-yellow-300 rounded-lg p-4">
                  {selectedMetadata.isTextarea ? (
                    <textarea
                      className="w-full px-3 py-2 border border-yellow-400 rounded-lg focus:ring-2 focus:ring-yellow-500 focus:border-transparent text-sm resize-none"
                      rows={selectedMetadata.rows || 4}
                      value={selectedMetadata.currentValue}
                      onChange={(e) => {
                        selectedMetadata.onChange(e.target.value)
                        onFormChange()
                      }}
                      onBlur={(e) => selectedMetadata.onBlur?.(e.target.value)}
                    />
                  ) : (
                    <input
                      type="text"
                      className="w-full px-3 py-2 text-left text-sm"
                      value={selectedMetadata.currentValue}
                      onChange={(e) => {
                        selectedMetadata.onChange(e.target.value)
                        onFormChange()
                      }}
                      onBlur={(e) => selectedMetadata.onBlur?.(e.target.value)}
                    />
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <h4 className="text-sm font-semibold text-museum-700 mb-2 flex items-center gap-2">
                    Original Metadata</h4>
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 min-h-[200px] max-h-[400px] overflow-y-auto">
                    <div className="whitespace-pre-wrap text-left text-sm text-museum-800 break-words">
                      {selectedMetadata.originalValue}
                    </div>
                  </div>
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-museum-700 mb-2 flex items-center gap-2">
                    AI Generated
                    <span className="inline-flex items-center justify-center rounded border border-museum-400 px-1 text-[10px]" aria-hidden="true">
                      AI
                    </span>
                  </h4>
                  <div className="bg-green-50 border border-green-200 rounded-lg p-4 min-h-[200px] max-h-[400px] overflow-y-auto">
                    <div className="whitespace-pre-wrap text-left text-sm text-museum-800 break-words">
                      {selectedMetadata.extractedValue}
                    </div>
                  </div>
                </div>
              </div>

            </div>

            <div className="px-6 py-4 border-t border-museum-200 bg-museum-50 flex justify-end">
              <button
                onClick={() => setSelectedMetadata(null)}
                className="px-4 py-2 bg-museum-600 text-white rounded-lg hover:bg-museum-700 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      
      )}
    </section>
  )
})

CorrectionsPanel.displayName = 'CorrectionsPanel'

export default CorrectionsPanel