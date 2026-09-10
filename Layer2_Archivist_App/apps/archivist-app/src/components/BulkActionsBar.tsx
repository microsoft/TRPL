'use client'

import { Download, Edit, CheckCircle } from 'lucide-react'
import { BulkActionState } from '@/types'

interface BulkActionsBarProps {
  bulkActions: BulkActionState
  onBulkAction: (action: string) => void
  onSelectAll: () => void
  onClearSelection: () => void
}

const BulkActionsBar: React.FC<BulkActionsBarProps> = ({
  bulkActions,
  onBulkAction,
  onSelectAll,
  onClearSelection
}) => {
  if (!bulkActions.isVisible) return null

  return (
    <section className="bg-museum-100 border-t border-museum-200 px-6 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-4">
          <span className="text-sm font-medium text-museum-900">
            {bulkActions.selectedItems.length} item{bulkActions.selectedItems.length !== 1 ? 's' : ''} selected
          </span>
          <div className="w-px h-4 bg-museum-300" aria-hidden="true"></div>
          <button
            className="text-sm text-museum-700 hover:text-museum-900 font-medium"
            onClick={onSelectAll}
          >
            Select All
          </button>
          <button
            className="text-sm text-museum-700 hover:text-museum-900 font-medium"
            onClick={onClearSelection}
          >
            Clear Selection
          </button>
        </div>
        <div className="flex items-center space-x-2">
          <button
            className="px-4 py-2 bg-white border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors flex items-center space-x-2"
            onClick={() => onBulkAction('export')}
          >
            <Download className="w-4 h-4" />
            <span>Export</span>
          </button>
          <button
            className="px-4 py-2 bg-museum-800 text-white rounded-lg hover:bg-museum-700 transition-colors flex items-center space-x-2"
            onClick={() => onBulkAction('edit')}
          >
            <Edit className="w-4 h-4" />
            <span>Bulk Edit</span>
          </button>
          <button
            className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors flex items-center space-x-2"
            onClick={() => onBulkAction('complete')}
          >
            <CheckCircle className="w-4 h-4" />
            <span>Mark Complete</span>
          </button>
        </div>
      </div>
    </section>
  )
}

export default BulkActionsBar
