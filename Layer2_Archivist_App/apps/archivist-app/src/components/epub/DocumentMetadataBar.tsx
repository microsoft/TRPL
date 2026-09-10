import React from 'react'
import { Book, Building, Globe, XCircle, Filter, Hash, FileText } from 'lucide-react'
import EpubStatusBadge from './EpubStatusBadge'
import type { EpubDocument } from './types'

interface DocumentMetadataBarProps {
  document: EpubDocument
  onClose: () => void
  onOpenFilters?: () => void
  showFilterButton?: boolean
  showChunksInfo?: boolean
}

const DocumentMetadataBar: React.FC<DocumentMetadataBarProps> = ({
  document,
  onClose,
  onOpenFilters,
  showFilterButton = false,
  showChunksInfo = false
}) => {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 px-4 py-3 flex-shrink-0">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-1">
            <Book className="w-5 h-5 text-indigo-600 flex-shrink-0" />
            <h2 className="font-semibold text-gray-900">
              {document.metadata?.title || document.filename}
            </h2>
            {document.metadata?.author && (
              <span className="text-gray-500 text-sm">
                by <span className="font-medium text-gray-700">{document.metadata.author}</span>
              </span>
            )}
          </div>
          {/* Metadata row */}
          <div className="flex items-center gap-4 text-xs text-gray-500 ml-7">
            {document.metadata?.publisher && (
              <span className="flex items-center gap-1">
                <Building className="w-3 h-3" />
                {document.metadata.publisher}
              </span>
            )}
            {document.metadata?.language && (
              <span className="flex items-center gap-1">
                <Globe className="w-3 h-3" />
                {document.metadata.language}
              </span>
            )}
            {(document.metadata?.chapter_count ?? 0) > 0 && (
              <span>{document.metadata?.chapter_count} chapters</span>
            )}
            {document.metadata?.isbn && (
              <span>ISBN: {document.metadata.isbn}</span>
            )}
            {/* Show chunks info for completed documents */}
            {showChunksInfo && document.ingested_chunks && (
              <>
                <span className="flex items-center gap-1 text-green-600">
                  <Hash className="w-3 h-3" />
                  {document.ingested_chunks} chunks indexed
                </span>
                {document.ingested_word_count && (
                  <span className="flex items-center gap-1 text-green-600">
                    <FileText className="w-3 h-3" />
                    {document.ingested_word_count.toLocaleString()} words
                  </span>
                )}
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {showFilterButton && onOpenFilters && (
            <button
              onClick={onOpenFilters}
              className="px-2 py-1 text-xs text-gray-600 hover:text-indigo-600 hover:bg-indigo-50 rounded flex items-center gap-1 transition-colors"
              title="Configure content filters"
            >
              <Filter className="w-3.5 h-3.5" />
              Filters
            </button>
          )}
          <EpubStatusBadge status={document.status} size="md" />
          <button
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors"
          >
            <XCircle className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )
}

export default DocumentMetadataBar

