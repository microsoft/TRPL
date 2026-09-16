// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState, useMemo } from 'react'
import { FileText, Edit, GitCompare, FileSearch } from 'lucide-react'
import { diffWords } from 'diff'
import { ApiDocumentMetadata, AssetDetail } from '@/services/api'

interface OCRTabsProps {
  originalText: string
  modifiedText: string
  onModifiedTextChange: (text: string) => void
  onFormChange: () => void
  onModifiedTextBlur?: (text: string) => void
  compareOriginalText?: string
  compareModifiedText?: string
  currentAsset?: AssetDetail
  documentData?: ApiDocumentMetadata | null
}

type TabType = 'original' | 'modified' | 'compare'

const OCRTabs: React.FC<OCRTabsProps> = ({
  originalText,
  modifiedText,
  onModifiedTextChange,
  onFormChange,
  onModifiedTextBlur,
  compareOriginalText,
  compareModifiedText,
  currentAsset,
  documentData
}) => {
  const [activeTab, setActiveTab] = useState<TabType>('modified')

  const differences = useMemo(() => {
    const originalTextForCompare = compareOriginalText !== undefined ? compareOriginalText : originalText
    const modifiedTextForCompare = compareModifiedText !== undefined ? compareModifiedText : modifiedText

    if (!originalTextForCompare && !modifiedTextForCompare) return []

    // Compare modified to original to see what changed
    return diffWords(modifiedTextForCompare, originalTextForCompare)
  }, [originalText, modifiedText, compareOriginalText, compareModifiedText])

  const handleModifiedTextChange = (value: string) => {
    onModifiedTextChange(value)
    onFormChange()
  }

  // Calculate OCR Confidence
  const ocrConf = currentAsset?.ocr_result?.confidence_scores?.ocr_confidence 
    ?? documentData?.asset_avg_confidence 
    ?? 0;
  const percentage = Math.round(ocrConf * 100);
  const color = percentage >= 90 ? 'text-green-600' : 
                percentage >= 70 ? 'text-yellow-600' : 
                percentage >= 50 ? 'text-orange-600' : 'text-red-600';
  const bgColor = percentage >= 90 ? 'bg-green-500' : 
                  percentage >= 70 ? 'bg-yellow-500' : 
                  percentage >= 50 ? 'bg-orange-500' : 'bg-red-500';

  return (
    <div className="px-6 py-6">
      {/* Tabs */}
      <div className="flex items-center justify-between mb-4 border-b border-museum-200">
        <div className="flex items-center space-x-1">
          {[
            { id: 'original', icon: FileText, label: 'Original OCR' },
            { id: 'modified', icon: Edit, label: 'Modified Text' },
            { id: 'compare', icon: GitCompare, label: 'Compare View' }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as TabType)}
              className={`flex items-center space-x-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-museum-600 text-museum-900'
                  : 'border-transparent text-museum-500 hover:text-museum-700 hover:border-museum-300'
              }`}
            >
              <tab.icon className="w-4 h-4" />
              <span>{tab.label}</span>
            </button>
          ))}
        </div>
        
        {/* OCR Confidence - Next to Compare View */}
        <div className="flex items-center gap-2">
          <FileSearch className="w-4 h-4 text-gray-500" />
          <span className="text-gray-600 text-sm">OCR Confidence:</span>
          <div className="flex items-center gap-2">
            {/* <div className="w-20 h-1.5 bg-gray-200 rounded-full overflow-hidden">
              <div className={`h-full ${bgColor} rounded-full transition-all`} style={{ width: `${percentage}%` }} />
            </div> */}
            <span className={`font-medium text-sm ${color}`}>{percentage}%</span>
          </div>
        </div>
      </div>

      {/* Tab Content */}
      <div className="relative">
        {activeTab === 'original' && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-semibold text-museum-900">
                Original OCR Text (Read-only)
              </label>
              <span className="text-xs text-museum-500">
                {originalText.length.toLocaleString()} characters
              </span>
            </div>
            <div className="w-full h-80 px-4 py-3 border border-museum-300 rounded-lg bg-museum-50 overflow-y-auto ocr-scrollbar text-sm leading-relaxed text-left whitespace-pre-wrap">
              {originalText || (
                <span className="text-museum-400 italic">No OCR text available</span>
              )}
            </div>
          </div>
        )}

        {activeTab === 'modified' && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-semibold text-museum-900">Modified Text (Editable)</label>
              <span className="text-xs text-museum-500">
                {modifiedText.length.toLocaleString()} characters
              </span>
            </div>
            <textarea
              className="w-full h-80 px-4 py-3 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent resize-none text-sm leading-relaxed ocr-scrollbar"
              placeholder="Edit the OCR text here..."
              value={modifiedText}
              onChange={(e) => handleModifiedTextChange(e.target.value)}
              onBlur={(e) => onModifiedTextBlur?.(e.target.value)}
            />
          </div>
        )}

        {activeTab === 'compare' && (
          <div>
            <div className="mb-4">
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-semibold text-museum-900">Text Comparison</label>
                <div className="flex items-center space-x-4 text-xs">
                  <div className="flex items-center space-x-1">
                    <div className="w-3 h-3 bg-green-200 border border-green-400 rounded" />
                    <span className="text-museum-600">Added</span>
                  </div>
                  <div className="flex items-center space-x-1">
                    <div className="w-3 h-3 bg-red-200 border border-red-400 rounded" />
                    <span className="text-museum-600">Removed</span>
                  </div>
                  <div className="flex items-center space-x-1">
                    <div className="w-3 h-3 bg-yellow-200 border border-yellow-400 rounded" />
                    <span className="text-museum-600">Changed</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Compare Columns */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <h4 className="text-xs font-semibold text-museum-700 mb-2 uppercase">Original</h4>
                <div className="w-full h-80 px-4 py-3 border border-museum-300 rounded-lg bg-museum-50 overflow-y-auto ocr-scrollbar text-sm leading-relaxed text-left">
                  <div className="whitespace-pre-wrap">
                    {(compareOriginalText !== undefined ? compareOriginalText : originalText) || (
                      <span className="text-museum-400 italic">No original text</span>
                    )}
                  </div>
                </div>
              </div>

              <div>
                <h4 className="text-xs font-semibold text-museum-700 mb-2 uppercase">Modified</h4>
                <div className="w-full h-80 px-4 py-3 border border-museum-300 rounded-lg bg-white overflow-y-auto ocr-scrollbar text-sm leading-relaxed text-left">
                  {differences.length > 0 ? (
                    <div className="whitespace-pre-wrap">
                      {differences.map((diff, i) =>
                        diff.removed ? (
                          <span key={i} className="bg-green-200 text-green-900 px-0.5">
                            {diff.value}
                          </span>
                        ) : diff.added ? (
                          <span key={i} className="bg-red-200 text-red-900 px-0.5">
                            {diff.value}
                          </span>
                        ) : (
                          <span key={i}>{diff.value}</span>
                        )
                      )}
                    </div>
                  ) : (
                    <span className="text-museum-400 italic">No modified text</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default OCRTabs
