import { XCircle, AlertTriangle } from 'lucide-react'
import ModalPortal from '@/components/ModalPortal'
import type { RetryConfig, PipelineStage } from '../../types'

interface RetryConfigModalProps {
  isOpen: boolean
  config: RetryConfig
  collectionInput: string
  stages: PipelineStage[]  // Dynamic stages from backend
  onConfigChange: (config: RetryConfig) => void
  onCollectionInputChange: (value: string) => void
  onAddCollection: () => void
  onRemoveCollection: (index: number) => void
  onClose: () => void
  onSubmit: () => void
}

export default function RetryConfigModal({
  isOpen,
  config,
  collectionInput,
  stages,
  onConfigChange,
  onCollectionInputChange,
  onAddCollection,
  onRemoveCollection,
  onClose,
  onSubmit
}: RetryConfigModalProps) {
  if (!isOpen) return null

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[100] overflow-x-hidden overflow-y-auto overscroll-y-contain">
        <div className="fixed inset-0 bg-gray-500/75 backdrop-blur-sm" onClick={onClose} aria-hidden />
        <div className="relative z-[1] flex min-h-full items-center justify-center p-4">
          <div className="relative w-full max-w-lg rounded-xl bg-white p-6 shadow-2xl">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Retry Failed Records</h3>
          
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Stage to Retry
              </label>
              <select
                value={config.step}
                onChange={(e) => onConfigChange({ ...config, step: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              >
                <option value="all">All Stages</option>
                {stages
                  .filter(s => s.canRetry !== false) // Only show stages that can be retried (based on config)
                  .map(stage => (
                    <option key={stage.id} value={stage.id}>{stage.name}</option>
                  ))
                }
              </select>
            </div>

            {/* Source collection filter */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Source Collection IDs
              </label>
              <div className="flex gap-2 mb-2">
                <input
                  type="text"
                  value={collectionInput}
                  onChange={(e) => onCollectionInputChange(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), onAddCollection())}
                  placeholder="e.g., fictional-correspondence"
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm"
                />
                <button
                  onClick={onAddCollection}
                  className="px-3 py-2 bg-amber-100 text-amber-700 rounded-lg hover:bg-amber-200 transition-colors"
                >
                  Add
                </button>
              </div>
              {((config.collection_ids || []) as string[]).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {((config.collection_ids || []) as string[]).map((value: string, idx: number) => (
                    <span key={idx} className="inline-flex items-center gap-1 px-2 py-1 bg-amber-50 text-amber-700 rounded text-sm">
                      {value}
                      <button
                        onClick={() => onRemoveCollection(idx)}
                        className="text-amber-400 hover:text-amber-600"
                      >
                        <XCircle className="w-4 h-4" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <p className="text-xs text-gray-400 mt-1">Leave empty to include all source collections</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Retries
              </label>
              <input
                type="number"
                value={config.max_retries}
                onChange={(e) => onConfigChange({ ...config, max_retries: parseInt(e.target.value) || 3 })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                min={1}
                max={10}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Batch Size
              </label>
              <input
                type="number"
                value={config.batch_size}
                onChange={(e) => onConfigChange({ ...config, batch_size: parseInt(e.target.value) || 50 })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                min={10}
                max={200}
              />
            </div>

            {/* Force Retry Option */}
            <div className="pt-2">
              <label className="flex items-center gap-3 p-3 bg-gray-50 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-100 transition-colors">
                <div className="relative flex items-center justify-center">
                  <input
                    type="checkbox"
                    checked={config.force_retry || false}
                    onChange={(e) => onConfigChange({ ...config, force_retry: e.target.checked })}
                    className="peer sr-only"
                  />
                  <div className="w-5 h-5 bg-white border-2 border-gray-300 rounded peer-checked:bg-red-600 peer-checked:border-red-600 peer-focus:ring-2 peer-focus:ring-red-500 peer-focus:ring-offset-1 transition-colors" />
                  <svg 
                    className="absolute w-3 h-3 text-white opacity-0 peer-checked:opacity-100 pointer-events-none transition-opacity" 
                    fill="none" 
                    stroke="currentColor" 
                    viewBox="0 0 24 24"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <span className="text-sm font-medium text-gray-700">Force Retry (Reprocess All)</span>
              </label>
              
              {config.force_retry && (
                <div className="mt-2 p-3 bg-red-50 border border-red-200 rounded-lg">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                    <div className="text-sm text-red-700">
                      <p className="font-medium">Warning: Force retry will:</p>
                      <ul className="list-disc list-inside mt-1 space-y-0.5 text-red-600">
                        <li>Reprocess ALL records (both failed and completed)</li>
                        <li>Ignore max retries limit</li>
                        <li>Reset all retry counts to 0</li>
                      </ul>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="flex justify-end gap-3 mt-6">
            <button
              onClick={onClose}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={onSubmit}
              className="px-4 py-2 bg-amber-500 text-white rounded-lg font-medium hover:bg-amber-600 transition-colors"
            >
              Start Retry
            </button>
          </div>
          </div>
        </div>
      </div>
    </ModalPortal>
  )
}
