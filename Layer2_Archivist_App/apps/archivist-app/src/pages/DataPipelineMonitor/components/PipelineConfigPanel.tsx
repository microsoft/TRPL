// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { Settings2, Loader2, CheckCircle2, XCircle, RotateCcw } from 'lucide-react'
import type { StageConfig } from '../types'

interface PipelineConfigPanelProps {
  config: StageConfig
  onConfigChange: (config: StageConfig) => void
  collectionInput: string
  onCollectionInputChange: (value: string) => void
  onAddCollection: () => void
  onRemoveCollection: (index: number) => void
  isLoading: boolean
  isSaving: boolean
  isResetting: boolean
  lastSaved: string | null
  onSave: () => void
  onReset: () => void
}

export default function PipelineConfigPanel({
  config,
  onConfigChange,
  collectionInput,
  onCollectionInputChange,
  onAddCollection,
  onRemoveCollection,
  isLoading,
  isSaving,
  isResetting,
  lastSaved,
  onSave,
  onReset
}: PipelineConfigPanelProps) {
  return (
    <div className="bg-gradient-to-r from-slate-50 to-gray-50 border-b border-gray-200 px-6 py-5">
      <div className="max-w-4xl">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Settings2 className="w-5 h-5 text-gray-600" />
            <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Pipeline Configuration</h3>
            <span className="text-xs text-gray-500 bg-gray-200 px-2 py-0.5 rounded-full">Applied to all stages</span>
            {isLoading && (
              <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />
            )}
          </div>
          <div className="flex items-center gap-3">
            {lastSaved && (
              <span className="text-xs text-gray-400">
                Saved: {new Date(lastSaved).toLocaleString()}
              </span>
            )}
            <button
              onClick={onReset}
              disabled={isResetting || isLoading || isSaving}
              className="inline-flex items-center gap-2 px-4 py-2 text-red-600 bg-red-50 border border-red-200 rounded-lg font-medium hover:bg-red-100 disabled:opacity-50 transition-colors text-sm"
              title="Reset configuration to defaults"
            >
              {isResetting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Resetting...
                </>
              ) : (
                <>
                  <RotateCcw className="w-4 h-4" />
                  Reset
                </>
              )}
            </button>
            <button
              onClick={onSave}
              disabled={isSaving || isLoading || isResetting}
              className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700 disabled:opacity-50 transition-colors text-sm"
            >
              {isSaving ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-4 h-4" />
                  Save Config
                </>
              )}
            </button>
          </div>
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Source collection filter */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Source Collection IDs
            </label>
            <div className="flex gap-2 mb-2">
              <input
                type="text"
                value={collectionInput}
                onChange={(e) => onCollectionInputChange(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), onAddCollection())}
                placeholder="e.g., fictional-correspondence"
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm bg-white"
                disabled={isLoading}
              />
              <button
                onClick={onAddCollection}
                disabled={isLoading}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors text-sm font-medium disabled:opacity-50"
              >
                Add
              </button>
            </div>
            {((config.collection_ids || []) as string[]).length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {((config.collection_ids || []) as string[]).map((value: string, idx: number) => (
                  <span key={idx} className="inline-flex items-center gap-1 px-3 py-1 bg-indigo-100 text-indigo-800 rounded-full text-sm font-medium">
                    {value}
                    <button
                      onClick={() => onRemoveCollection(idx)}
                      className="text-indigo-500 hover:text-indigo-700 ml-1"
                    >
                      <XCircle className="w-4 h-4" />
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-500 italic">All source collections</p>
            )}
          </div>

          {/* Batch Size & Parallel Batches */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Batch Size
              </label>
              <input
                type="number"
                value={config.batch_size || 100}
                onChange={(e) => onConfigChange({ ...config, batch_size: parseInt(e.target.value) || 100 })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 bg-white"
                min={10}
                max={500}
                disabled={isLoading}
              />
              <p className="text-xs text-gray-500 mt-1">Records per batch</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Parallel Batches
              </label>
              <input
                type="number"
                value={config.parallel_batches || 20}
                onChange={(e) => onConfigChange({ ...config, parallel_batches: parseInt(e.target.value) || 20 })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 bg-white"
                min={1}
                max={50}
                disabled={isLoading}
              />
              <p className="text-xs text-gray-500 mt-1">For Content Source Sync only</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
