// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import {
  FileText,
  Calendar,
  User,
  Building,
  Hash,
  AlertTriangle,
  Clock,
  CheckCircle,
  Save,
  Check,
  Loader2,
  ArrowLeft,
  ArrowRight
} from 'lucide-react'

interface DocumentHeaderProps {
  title: string
  creationDate: string
  creator: string
  collection: string
  repository: string
  identifier: string
  resourceType: string
  severeDeviation?: boolean
  status?: string
  onSave: () => void
  onSaveAndComplete: () => void
  // Navigation props
  onPreviousRecord?: () => void
  onNextRecord?: () => void
  currentRecordIndex?: number
  totalRecords?: number
  showNavigation?: boolean
  // New metadata props
  datePublishedToPortal?: string
  sourceRecordId?: string
}

const DocumentHeader: React.FC<DocumentHeaderProps> = ({
  title,
  creationDate,
  creator,
  collection,
  repository,
  identifier,
  resourceType,
  severeDeviation,
  status,
  onSave,
  onSaveAndComplete,
  onPreviousRecord,
  onNextRecord,
  currentRecordIndex = -1,
  totalRecords = 0,
  showNavigation = false,
  datePublishedToPortal,
  sourceRecordId
}) => {
  // Helper function to safely convert any value to a string
  const toString = (value: any): string => {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'object') {
      // Try common object property names
      if (value.label) return value.label;
      if (value.name) return value.name;
      if (value.title) return value.title;
      if (value.text) return value.text;
    }
    return '';
  };

  const getResourceTypeIcon = (type: string) => {
    const lowerType = type.toLowerCase()
    if (lowerType.includes('letter') || lowerType.includes('correspondence')) {
      return <FileText className="w-6 h-6 text-white" />
    }
    if (lowerType.includes('photo') || lowerType.includes('image')) {
      return <FileText className="w-6 h-6 text-white" />
    }
    if (lowerType.includes('article') || lowerType.includes('news')) {
      return <FileText className="w-6 h-6 text-white" />
    }
    if (lowerType.includes('speech')) {
      return <FileText className="w-6 h-6 text-white" />
    }
    if (lowerType.includes('diary')) {
      return <FileText className="w-6 h-6 text-white" />
    }
    return <FileText className="w-6 h-6 text-white" />
  }

  // 🟩 Centralized status renderer (based on your shared logic)
  const renderStatusBadge = () => {
    const baseClasses =
      'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium'

    if (!status) return null

    const normalized = status.toLowerCase()

    if (normalized === 'published') {
      return (
        <span className={`${baseClasses} bg-green-100 text-green-800`}>
          <CheckCircle className="w-3 h-3 mr-1" />
          Published
        </span>
      )
    } else if (normalized === 'publishing') {
      return (
        <span className={`${baseClasses} bg-purple-100 text-purple-800`}>
          <Clock className="w-3 h-3 mr-1" />
          Publishing
        </span>
      )
    } else if (normalized === 'reviewed') {
      return (
        <span className={`${baseClasses} bg-blue-100 text-blue-800`}>
          <CheckCircle className="w-3 h-3 mr-1" />
          Reviewed
        </span>
      )
    } else if (normalized === 'failed' || normalized === 'error') {
      return (
        <span className={`${baseClasses} bg-red-100 text-red-800`}>
          <AlertTriangle className="w-3 h-3 mr-1" />
          Failed
        </span>
      )
    } else {
      return (
        <span className={`${baseClasses} bg-yellow-100 text-yellow-800`}>
          <Clock className="w-3 h-3 mr-1" />
          Pending
        </span>
      )
    }
  }

  return (
    <section className="bg-museum-green border-b border-museum-300 px-6 py-6">
      <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between space-y-4 xl:space-y-0">
        <div className="flex-1 flex items-center space-x-4">
          {/* Resource Icon */}
          <div className="w-12 h-12 bg-white/20 rounded-lg flex items-center justify-center flex-shrink-0">
            {getResourceTypeIcon(resourceType)}
          </div>

          {/* Document Info */}
          <div className="min-w-0 flex-1 text-center xl:text-left">
            <h2 className="text-2xl font-semibold text-white mb-1 flex items-center space-x-2 md:space-x-4 lg:space-x-6">
              <span>{title || 'Untitled Document'}</span>
              {sourceRecordId && (
                <span className="text-xs text-museum-200">
                  Source ID: {sourceRecordId}
                </span>
              )}
            </h2>

            <div className="flex flex-wrap justify-center xl:justify-start items-center gap-4 text-sm text-white/90 mt-3">
              {creationDate && (
                <div className="flex items-center space-x-1">
                  <Calendar className="w-3 h-3 text-white" />
                  <span>{toString(creationDate)}</span>
                </div>
              )}
              {creator && (
                <div className="flex items-center space-x-1">
                  <User className="w-3 h-3 text-white" />
                  <span>{toString(creator)}</span>
                </div>
              )}
              {repository && (
                <div className="flex items-center space-x-1">
                  <Building className="w-3 h-3 text-white" />
                  <span>{toString(repository)}</span>
                </div>
              )}
              {collection && (
                <div className="flex items-center space-x-1">
                  <Building className="w-3 h-3 text-white" />
                  <span>{toString(collection)}</span>
                </div>
              )}
              {identifier && (
                <div className="flex items-center space-x-1">
                  <Hash className="w-3 h-3 text-white" />
                  <span>{toString(identifier)}</span>
                </div>
              )}
              {/* Date Published to Portal */}
            {datePublishedToPortal && (
                <div className="flex items-center space-x-1">
                  <Calendar className="w-3 h-3 text-white" />
                  <span>Date Published to Portal: {toString(datePublishedToPortal)}</span>
                </div>
            )}
              <div className="flex items-center space-x-1 w-3 h-3">
                {renderStatusBadge()}
              </div>
            </div>
            
            
          </div>
        </div>

        {/* Navigation Buttons */}
        {showNavigation && totalRecords > 0 && currentRecordIndex >= 0 && (
          <div className="flex items-center space-x-4">
            <button
              onClick={onPreviousRecord}
              disabled={currentRecordIndex === 0}
              className="flex items-center space-x-2 px-4 py-2 bg-museum-accent text-white rounded-lg hover:bg-museum-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              <span>Previous</span>
            </button>
            
            <div className="text-sm text-white">
              Record {currentRecordIndex + 1} of {totalRecords}
            </div>
            
            <button
              onClick={onNextRecord}
              disabled={currentRecordIndex >= totalRecords - 1}
              className="flex items-center space-x-2 px-4 py-2 bg-museum-accent text-white rounded-lg hover:bg-museum-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <span>Next</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </section>
  )
}

export default DocumentHeader
