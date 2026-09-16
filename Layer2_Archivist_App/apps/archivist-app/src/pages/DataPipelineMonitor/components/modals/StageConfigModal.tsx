// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { PlayCircle } from 'lucide-react'
import ModalPortal from '@/components/ModalPortal'
import type { StageConfig, ExtraStageConfig, PipelineStage } from '../../types'

interface StageConfigModalProps {
  isOpen: boolean
  stage: PipelineStage | null
  globalConfig: StageConfig
  extraConfig: ExtraStageConfig
  onExtraConfigChange: (config: ExtraStageConfig) => void
  onClose: () => void
  onSubmit: () => void
}

export default function StageConfigModal({
  isOpen,
  stage,
  globalConfig,
  extraConfig,
  onExtraConfigChange,
  onClose,
  onSubmit
}: StageConfigModalProps) {
  if (!isOpen || !stage) return null

  // Helper to update a config field
  const updateField = (field: string, value: any) => {
    onExtraConfigChange({ ...extraConfig, [field]: value })
  }

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[100] overflow-x-hidden overflow-y-auto overscroll-y-contain">
        <div className="fixed inset-0 bg-gray-500/75 backdrop-blur-sm" onClick={onClose} aria-hidden />
        <div className="relative z-[1] flex min-h-full items-center justify-center p-4">
          <div className="relative w-full max-w-md rounded-xl bg-white p-6 shadow-2xl">
          <h3 className="text-lg font-semibold text-gray-900 mb-2">
            Configure {stage.name}
          </h3>
          <p className="text-sm text-gray-500 mb-4">
            {stage.description}. Global config settings will be merged.
          </p>
          
          {/* Show current global config */}
          <div className="mb-4 p-3 bg-gray-50 rounded-lg text-sm">
            <div className="text-gray-600">
              <strong>Global config:</strong>
              <div className="mt-1 text-xs">
                {(globalConfig.collection_ids || []).length > 0
                  ? `Source collections: ${globalConfig.collection_ids?.join(', ')}`
                  : 'All source collections'}
              </div>
              <div className="text-xs">Batch size: {globalConfig.batch_size || 100}</div>
              <div className="text-xs">Parallel batches: {globalConfig.parallel_batches || 20}</div>
            </div>
          </div>
          
          {/* Generic extra config fields */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Skip Records
              </label>
              <input
                type="number"
                value={extraConfig.skip || 0}
                onChange={(e) => updateField('skip', parseInt(e.target.value) || 0)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                min={0}
              />
              <p className="text-xs text-gray-400 mt-1">Leave 0 to auto-detect</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Total Records (Optional)
              </label>
              <input
                type="number"
                value={extraConfig.total || ''}
                onChange={(e) => updateField('total', e.target.value ? parseInt(e.target.value) : null)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                min={0}
                placeholder="Auto-detect from API"
              />
              <p className="text-xs text-gray-400 mt-1">Leave empty for auto-detection</p>
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
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 transition-colors"
            >
              <PlayCircle className="w-4 h-4" />
              Start {stage.name}
            </button>
          </div>
          </div>
        </div>
      </div>
    </ModalPortal>
  )
}
