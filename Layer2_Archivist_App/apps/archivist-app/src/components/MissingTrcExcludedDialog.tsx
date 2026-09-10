import React from 'react'
import { Info, X } from 'lucide-react'

export interface MissingTrcExcludedDialogProps {
  isOpen: boolean
  onClose: () => void
  total: number
  recordIds: string[]
  idsTruncated: boolean
}

/**
 * Shown after bulk ingest when some records were excluded because metadata has no usable
 * Date Published to Portal (TRC) — e.g. ingest-by-query or publish-selected with a mixed selection.
 */
const MissingTrcExcludedDialog: React.FC<MissingTrcExcludedDialogProps> = ({
  isOpen,
  onClose,
  total,
  recordIds,
  idsTruncated,
}) => {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <button
        type="button"
        className="absolute inset-0 bg-black/40"
        aria-label="Close"
        onClick={onClose}
      />
      <div className="relative bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[85vh] flex flex-col border border-amber-100">
        <div className="flex items-start gap-3 p-5 border-b border-gray-100">
          <div className="w-10 h-10 rounded-lg bg-amber-50 flex items-center justify-center flex-shrink-0">
            <Info className="w-5 h-5 text-amber-700" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900">Some records were not ingested</h2>
            <p className="text-sm text-gray-600 mt-1">
              {total === 1
                ? '1 record was not queued because it does not have a usable Date Published to Portal (TRC) in metadata.'
                : `${total} records were not queued because they do not have a usable Date Published to Portal (TRC) in metadata.`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-lg text-gray-500 hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="px-5 py-3 flex-1 min-h-0 flex flex-col">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Document IDs (Cosmos)</p>
          <pre className="text-xs font-mono bg-gray-50 border border-gray-200 rounded-lg p-3 overflow-auto flex-1 max-h-[40vh] whitespace-pre-wrap break-all">
            {recordIds.length > 0 ? recordIds.join('\n') : '—'}
          </pre>
          {idsTruncated && (
            <p className="text-xs text-amber-800 mt-2">
              Showing the first {recordIds.length} IDs. The full list is larger; use admin tools or search to locate
              remaining records.
            </p>
          )}
        </div>
        <div className="p-5 border-t border-gray-100 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-white bg-amber-600 rounded-lg hover:bg-amber-700"
          >
            OK
          </button>
        </div>
      </div>
    </div>
  )
}

export default MissingTrcExcludedDialog
