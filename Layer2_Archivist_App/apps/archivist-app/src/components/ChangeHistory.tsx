import React, { useEffect, useState, useImperativeHandle, forwardRef, useCallback } from 'react'
import { Clock, User, Edit3, AlertCircle } from 'lucide-react'
import { apiService, AuditEntry } from '@/services/api'

interface ChangeHistoryProps {
  documentId: string
  onRowClick?: (change: ChangeDetail) => void
}

export interface ChangeHistoryRef {
  refetch: () => Promise<void>
}

export interface ChangeDetail {
  field: string
  fieldPath: string
  oldValue: string
  newValue: string
  changedBy: string
  changedAt: string
  version: number
}

const ChangeHistory = forwardRef<ChangeHistoryRef, ChangeHistoryProps>(({ documentId, onRowClick }, ref) => {
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

  const fetchAuditHistory = useCallback(async () => {
    if (!documentId) return
    try {
      setIsLoading(true)
      setError(null)
      const response = await apiService.getAuditHistory(documentId, 50)
      setAuditEntries(response.entries)
    } catch (err) {
      console.error('Failed to fetch audit history:', err)
      setError('Failed to load change history')
    } finally {
      setIsLoading(false)
    }
  }, [documentId])

  useEffect(() => {
    fetchAuditHistory()
  }, [fetchAuditHistory])

  useImperativeHandle(ref, () => ({
    refetch: fetchAuditHistory
  }), [fetchAuditHistory])


  const formatDate = (isoString: string) =>
    new Intl.DateTimeFormat('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    }).format(new Date(isoString))

  const truncateText = (text: any, maxLength: number = 50) => {
    // Handle null, undefined, or non-string values
    if (text === null || text === undefined) return ''
    
    // Convert to string if it's not already (handles booleans, numbers, etc.)
    const textStr = typeof text === 'string' ? text : String(text)
    
    // Return truncated or full text
    return textStr.length <= maxLength ? textStr : textStr.substring(0, maxLength) + '...'
  }

  const getFieldDisplayName = (path: string) => {
    if (path.includes('/ocr_result/ocr_text')) return 'OCR Text'
    if (path.includes('visual_detailed_description_flexible')) return 'Visual Description'
    const parts = path.split('/')
    return parts[parts.length - 1] || path
  }

  const flattenedChanges = auditEntries.flatMap(entry =>
    entry.fields.map(field => ({
      id: `${entry.id}-${field.path}`,
      entryId: entry.id,
      field: getFieldDisplayName(field.path),
      fieldPath: field.path,
      oldValue: field.from || '',
      newValue: field.to || '',
      changedBy: entry.by.display,
      changedAt: entry.ts,
      version: entry.version
    }))
  )

  const displayedChanges = showAll ? flattenedChanges : flattenedChanges.slice(0, 2)

  return (
  <div className="w-full bg-white border-t border-museum-200">
    <div className="px-6 py-6">

      {/* Loading / Error / No Data States */}
      {isLoading ? (
        <div className="text-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-museum-600 mx-auto"></div>
          <p className="text-museum-500 mt-2">Loading change history...</p>
        </div>
      ) : error ? (
        <div className="text-center py-8">
          <AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-2" />
          <p className="text-red-600">{error}</p>
        </div>
      ) : flattenedChanges.length === 0 ? (
        <div className="text-center py-8 text-museum-500">
          <p>No changes recorded yet</p>
        </div>
      ) : (
        <>
          {/* Table Section */}
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-center">
              <thead>
                <tr className="bg-museum-50 border-b border-museum-200">
                  <th className="px-4 py-3 text-sm font-semibold text-museum-700">Field Changed</th>
                  <th className="px-4 py-3 text-sm font-semibold text-museum-700">Previous Value</th>
                  <th className="px-4 py-3 text-sm font-semibold text-museum-700">New Value</th>
                  <th className="px-4 py-3 text-sm font-semibold text-museum-700">Changed By</th>
                  <th className="px-4 py-3 text-sm font-semibold text-museum-700">Date & Time</th>
                  <th className="px-4 py-3 text-sm font-semibold text-museum-700">Version</th>
                </tr>
              </thead>
              <tbody>
                {displayedChanges.map((change, index) => (
                  <tr
                    key={change.id}
                    onClick={() => {
                      if (onRowClick) {
                        onRowClick(change)
                      }
                    }}
                    className={`border-b border-museum-100 hover:bg-museum-100 transition-colors cursor-pointer ${
                      index % 2 === 0 ? "bg-white" : "bg-museum-50"
                    }`}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-center space-x-2">
                        <Edit3 className="w-4 h-4 text-museum-500" />
                        <span className="font-medium text-museum-800">{change.field}</span>
                      </div>
                    </td>
                    <td
                      className="px-4 py-3 text-sm text-museum-600 truncate"
                      title={truncateText(change.oldValue, 1000)}
                    >
                      {truncateText(change.oldValue)}
                    </td>
                    <td
                      className="px-4 py-3 text-sm text-museum-800 truncate"
                      title={truncateText(change.newValue, 1000)}
                    >
                      {truncateText(change.newValue)}
                    </td>
                    <td className="px-4 py-3 text-sm text-museum-700">
                      <div className="flex items-center justify-center space-x-2">
                        <User className="w-4 h-4 text-museum-500" />
                        <span>{change.changedBy}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-museum-600">
                      {formatDate(change.changedAt)}
                    </td>
                    <td className="px-4 py-3 text-sm text-museum-500">v{change.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Show More / Less Toggle */}
          {flattenedChanges.length > 2 && (
            <div
              onClick={() => setShowAll(!showAll)}
              className={`flex justify-center mt-6 cursor-pointer select-none transition-colors ${
                showAll
                  ? "text-red-600 hover:text-red-800"
                  : "text-green-600 hover:text-green-800"
              }`}
            >
              <span>{showAll ? "Show Less ↑" : "Show More ↓"}</span>
            </div>
          )}
          
        </>
      )}
    </div>
  </div>
  )
})

ChangeHistory.displayName = 'ChangeHistory'
export default ChangeHistory
