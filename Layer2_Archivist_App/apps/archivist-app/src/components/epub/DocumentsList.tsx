import React from 'react'
import { FileText, Loader2, Trash2, RotateCcw } from 'lucide-react'
import EpubStatusBadge from './EpubStatusBadge'
import type { EpubDocument } from './types'

interface DocumentsListProps {
  documents: EpubDocument[]
  selectedDocumentId: string | null
  isLoading: boolean
  canEdit?: boolean
  onSelectDocument: (doc: EpubDocument) => void
  onDeleteDocument: (docId: string, filename: string) => void
  onRetryDocument: (docId: string, filename: string) => void
}

const DocumentsList: React.FC<DocumentsListProps> = ({
  documents,
  selectedDocumentId,
  isLoading,
  canEdit = true,
  onSelectDocument,
  onDeleteDocument,
  onRetryDocument
}) => {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 flex-1 flex flex-col min-h-0">
      <div className="px-3 py-2 border-b border-gray-100 flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-900">Documents ({documents.length})</span>
      </div>
      
      <div className="flex-1 overflow-y-auto scrollbar-custom">
        {isLoading ? (
          <div className="p-6 text-center">
            <Loader2 className="w-6 h-6 text-indigo-600 mx-auto animate-spin" />
          </div>
        ) : documents.length === 0 ? (
          <div className="p-6 text-center text-gray-400">
            <FileText className="w-8 h-8 mx-auto mb-1" />
            <p className="text-xs">No documents</p>
          </div>
        ) : (
          documents.map(doc => {
            const isSelected = selectedDocumentId === doc.id
            
            return (
              <div
                key={doc.id}
                onClick={() => onSelectDocument(doc)}
                className={`px-3 py-2 border-b border-gray-50 last:border-b-0 cursor-pointer transition-colors ${
                  isSelected ? 'bg-indigo-50' : 'hover:bg-gray-50'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-900 break-words">{doc.filename}</p>
                    <div className="flex items-center gap-1.5 mt-1">
                      <EpubStatusBadge status={doc.status} />
                      {doc.total_sections > 0 && (
                        <span className="text-xs text-gray-400" title="Selected / Total sections">
                          {doc.selected_sections}/{doc.total_sections} sections
                        </span>
                      )}
                    </div>
                    {doc.error_message && (
                      <p className="text-xs text-red-500 mt-1 line-clamp-2">{doc.error_message}</p>
                    )}
                  </div>
                  <div className="flex flex-col gap-1 flex-shrink-0">
                    {doc.status === 'error' && canEdit && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onRetryDocument(doc.id, doc.filename) }}
                        className="p-1 text-amber-500 hover:text-amber-600 hover:bg-amber-50 rounded transition-colors"
                        title="Retry"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                      </button>
                    )}
                    {canEdit && (
                      doc.status === 'deleting' ? (
                        <div className="p-1" title="Deleting...">
                          <Loader2 className="w-3.5 h-3.5 text-gray-400 animate-spin" />
                        </div>
                      ) : (
                        <button
                          onClick={(e) => { e.stopPropagation(); onDeleteDocument(doc.id, doc.filename) }}
                          className="p-1 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )
                    )}
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

export default DocumentsList

