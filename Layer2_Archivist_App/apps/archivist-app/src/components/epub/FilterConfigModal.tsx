// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useCallback } from 'react'
import { 
  Filter, 
  X, 
  Loader2, 
  CheckCircle2, 
  RotateCcw 
} from 'lucide-react'
import type { FilterConfig } from './types'

type FilterField = 'exclude_tags' | 'exclude_classes' | 'exclude_ids' | 'class_patterns'

interface FilterConfigModalProps {
  isOpen: boolean
  filterConfig: FilterConfig
  filterDefaults: FilterConfig | null
  isSaving: boolean
  isValidateMode: boolean
  onClose: () => void
  onChange: (config: FilterConfig) => void
  onSave: () => void
  onSaveAndReExtract: () => void
  onResetToDefaults: () => void
}

const FilterConfigModal: React.FC<FilterConfigModalProps> = ({
  isOpen,
  filterConfig,
  filterDefaults,
  isSaving,
  isValidateMode,
  onClose,
  onChange,
  onSave,
  onSaveAndReExtract,
  onResetToDefaults
}) => {
  const addFilterItem = useCallback((field: FilterField, value: string) => {
    if (!value.trim()) return
    const trimmed = value.trim()
    if (!filterConfig[field].includes(trimmed)) {
      onChange({
        ...filterConfig,
        [field]: [...filterConfig[field], trimmed]
      })
    }
  }, [filterConfig, onChange])

  const removeFilterItem = useCallback((field: FilterField, value: string) => {
    onChange({
      ...filterConfig,
      [field]: filterConfig[field].filter((v: string) => v !== value)
    })
  }, [filterConfig, onChange])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-indigo-100 rounded-lg flex items-center justify-center">
              <Filter className="w-5 h-5 text-indigo-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Filter Configuration</h2>
              <p className="text-sm text-gray-500">Configure what content to filter during extraction</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Exclude Tags */}
          <FilterSection
            label="Exclude HTML Tags"
            hint="Elements completely removed"
            items={filterConfig.exclude_tags}
            field="exclude_tags"
            prefix="<"
            suffix=">"
            colorClass="bg-red-100 text-red-700"
            hoverColor="hover:text-red-900"
            placeholder="Add tag (e.g., figcaption)"
            onAdd={addFilterItem}
            onRemove={removeFilterItem}
          />

          {/* Exclude Classes */}
          <FilterSection
            label="Exclude CSS Classes"
            hint="Elements with these classes removed"
            items={filterConfig.exclude_classes}
            field="exclude_classes"
            prefix="."
            colorClass="bg-amber-100 text-amber-700"
            hoverColor="hover:text-amber-900"
            placeholder="Add class (e.g., sidebar)"
            maxHeight="max-h-24"
            onAdd={addFilterItem}
            onRemove={removeFilterItem}
          />

          {/* Exclude IDs */}
          <FilterSection
            label="Exclude Element IDs"
            hint="Elements with these IDs removed"
            items={filterConfig.exclude_ids}
            field="exclude_ids"
            prefix="#"
            colorClass="bg-blue-100 text-blue-700"
            hoverColor="hover:text-blue-900"
            placeholder="Add ID (e.g., navigation)"
            onAdd={addFilterItem}
            onRemove={removeFilterItem}
          />

          {/* Class Patterns */}
          <FilterSection
            label="Class Patterns"
            hint='Partial matches (e.g., "ad-" matches "ad-banner")'
            items={filterConfig.class_patterns}
            field="class_patterns"
            prefix="*"
            suffix="*"
            colorClass="bg-purple-100 text-purple-700"
            hoverColor="hover:text-purple-900"
            placeholder="Add pattern (e.g., caption)"
            onAdd={addFilterItem}
            onRemove={removeFilterItem}
          />
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-between">
          <button
            onClick={onResetToDefaults}
            disabled={!filterDefaults}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
          >
            Reset to Defaults
          </button>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            {isValidateMode ? (
              <button
                onClick={onSaveAndReExtract}
                disabled={isSaving}
                className="px-4 py-2 text-sm bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 flex items-center gap-2 transition-colors"
              >
                {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
                Save & Re-extract
              </button>
            ) : (
              <button
                onClick={onSave}
                disabled={isSaving}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 flex items-center gap-2 transition-colors"
              >
                {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                Save Configuration
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// Reusable Filter Section Component
interface FilterSectionProps {
  label: string
  hint: string
  items: string[]
  field: FilterField
  prefix?: string
  suffix?: string
  colorClass: string
  hoverColor: string
  placeholder: string
  maxHeight?: string
  onAdd: (field: FilterField, value: string) => void
  onRemove: (field: FilterField, value: string) => void
}

const FilterSection: React.FC<FilterSectionProps> = ({
  label,
  hint,
  items,
  field,
  prefix = '',
  suffix = '',
  colorClass,
  hoverColor,
  placeholder,
  maxHeight,
  onAdd,
  onRemove
}) => {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-2">
        {label}
        <span className="text-xs text-gray-400 ml-2">{hint}</span>
      </label>
      <div className={`flex flex-wrap gap-1.5 mb-2 ${maxHeight ? `${maxHeight} overflow-y-auto` : ''}`}>
        {items.map(item => (
          <span key={item} className={`inline-flex items-center gap-1 px-2 py-1 ${colorClass} rounded text-sm`}>
            {prefix}{item}{suffix}
            <button onClick={() => onRemove(field, item)} className={hoverColor}>
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          placeholder={placeholder}
          className="flex-1 px-3 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              onAdd(field, e.currentTarget.value)
              e.currentTarget.value = ''
            }
          }}
        />
      </div>
    </div>
  )
}

export default FilterConfigModal

