// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState } from 'react'
import { 
  FileText, 
  Image, 
  Newspaper, 
  File, 
  CheckCircle,
  Clock,
  AlertCircle,
  User,
  ArrowUpDown,
  ArrowDown,
  Info
} from 'lucide-react'
import { ArchivalRecord, BulkActionState } from '@/types'

interface RecordsTableProps {
  records: ArchivalRecord[]
  bulkActions: BulkActionState
  onRecordClick: (record: ArchivalRecord) => void
  onBulkSelectionChange: (selectedIds: string[]) => void
}

const RecordsTable: React.FC<RecordsTableProps> = ({
  records,
  bulkActions,
  onRecordClick,
  onBulkSelectionChange
}) => {
  const [selectAll, setSelectAll] = useState(false)

  const handleSelectAll = () => {
    const newSelectAll = !selectAll
    setSelectAll(newSelectAll)
    onBulkSelectionChange(newSelectAll ? records?.map(r => r.id) : [])
  }

  const handleRecordSelect = (recordId: string, selected: boolean) => {
    const newSelection = selected
      ? [...bulkActions.selectedItems, recordId]
      : bulkActions.selectedItems.filter(id => id !== recordId)
    onBulkSelectionChange(newSelection)
    setSelectAll(newSelection.length === records.length)
  }

  const handleRecordClick = (record: ArchivalRecord) => {
    onRecordClick(record)
  }

  const getResourceIcon = (resourceType: ArchivalRecord['resourceType']) => {
    const type = (resourceType || '').toLowerCase();
    if (type.includes('letter') || type.includes('correspondence')) {
      return <FileText className="w-6 h-6 text-museum-600" />
    } else if (type.includes('photo') || type.includes('photograph') || type.includes('image')) {
      return <Image className="w-6 h-6 text-museum-600" />
    } else if (type.includes('article') || type.includes('news')) {
      return <Newspaper className="w-6 h-6 text-museum-600" />
    } else if (type.includes('speech') || type.includes('diary')) {
      return <File className="w-6 h-6 text-museum-600" />
    } else {
      return <FileText className="w-6 h-6 text-museum-600" />
    }
  }

  const getStatusBadge = (record: ArchivalRecord) => {
    const baseClasses = "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium"
    
    if (record.status === 'published') {
      return (
        <span className={`${baseClasses} bg-green-100 text-green-800`}>
          <CheckCircle className="w-3 h-3 mr-1" />
          Published
        </span>
      )
    } else if (record.status === 'publishing') {
      return (
        <span className={`${baseClasses} bg-purple-100 text-purple-800`}>
          <Clock className="w-3 h-3 mr-1" />
          Publishing
        </span>
      )
    } else if (record.status === 'reviewed') {
      return (
        <span className={`${baseClasses} bg-blue-100 text-blue-800`}>
          <CheckCircle className="w-3 h-3 mr-1" />
          Reviewed
        </span>
      )
    } else if (record.status === 'failed') {
      return (
        <span className={`${baseClasses} bg-red-100 text-red-800`}>
          <AlertCircle className="w-3 h-3 mr-1" />
          Failed
        </span>
      )
    } else if (record.status === 'pending') {
      return (
        <span className={`${baseClasses} bg-yellow-100 text-yellow-800`}>
          <Clock className="w-3 h-3 mr-1" />
          Pending
        </span>
      )
    }
  }

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 90) return 'text-green-600'
    if (confidence >= 70) return 'text-yellow-600'
    if (confidence >= 50) return 'text-orange-600'
    return 'text-red-600'
  }

  const getConfidenceBarColor = (confidence: number) => {
    if (confidence >= 90) return 'bg-green-500'
    if (confidence >= 70) return 'bg-yellow-500'
    if (confidence >= 50) return 'bg-orange-500'
    return 'bg-red-500'
  }

  const getConfidenceLabel = (confidence: number) => {
    if (confidence >= 90) return 'Very high confidence'
    if (confidence >= 70) return 'High confidence'
    if (confidence >= 50) return 'Medium confidence'
    return 'Low confidence'
  }

  return (
  <section className="bg-white">
    <div className="overflow-x-auto custom-scrollbar">
      <table className="w-full">
        <thead className="bg-museum-50 border-b border-museum-200">
  <tr>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900 w-12">
      <div className="flex items-center justify-center">
        <input
          type="checkbox"
          checked={selectAll}
          onChange={handleSelectAll}
          className="w-4 h-4 text-museum-600 bg-white border-museum-300 rounded focus:ring-museum-500 focus:ring-2 cursor-pointer"
          aria-label="Select all records"
        />
      </div>
    </th>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900 w-1/3">
      <div className="flex items-center justify-center space-x-1">
        <span>Title & Description</span>
        <ArrowUpDown className="w-3 h-3 text-museum-400" aria-hidden="true" />
      </div>
    </th>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900 w-36">
      <div className="flex items-center justify-center space-x-1">
        <span>Date Created</span>
        <ArrowDown className="w-3 h-3 text-museum-600" aria-hidden="true" />
      </div>
    </th>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Creator(s)</th>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900 w-50">
      <div className="flex flex-col">
        <span>Repository /</span>
        <span className="font-normal">Collection</span>
      </div>
    </th>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">
      <div className="flex items-center justify-center space-x-1">
        <span>AI Confidence</span>
        <Info className="w-3 h-3 text-museum-400" aria-hidden="true" />
      </div>
    </th>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Status</th>
    <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900 w-40">Last Updated</th>
  </tr>
</thead>

        <tbody className="divide-y divide-museum-200">
          {records?.map((record) => {
            const isSelected = bulkActions.selectedItems.includes(record.id || record.documentId)
            return (
            <tr
              key={record.id}
              className="hover:bg-museum-50 transition-colors group cursor-pointer"
              onClick={(e) => {
                // Don't trigger row click when clicking checkbox
                if ((e.target as HTMLElement).closest('input[type="checkbox"]')) {
                  return
                }
                handleRecordClick(record)
              }}
            >
              <td className="px-6 py-4">
                <div className="flex items-center justify-center">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={(e) => {
                      e.stopPropagation()
                      handleRecordSelect(record.id || record.documentId, !isSelected)
                    }}
                    onClick={(e) => e.stopPropagation()}
                    className="w-4 h-4 text-museum-600 bg-white border-museum-300 rounded focus:ring-museum-500 focus:ring-2 cursor-pointer"
                    aria-label={`Select record ${record.title}`}
                  />
                </div>
              </td>
              <td className="px-6 py-4">
                <div className="space-y-2">
                  <div className="flex items-start space-x-1">
                    <div className="w-12 h-12 bg-museum-100 rounded-lg flex items-center justify-center shrink-0">
                      {getResourceIcon(record.resourceType)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3 className="font-semibold text-sm text-museum-700 ">{record.title}</h3>
                      <p className="text-xs text-museum-600 line-clamp-2">{record.content}</p>
                      <div className="flex items-center space-x-2 mt-1">
                        {record.status === 'published' && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                            <CheckCircle className="w-3 h-3 mr-1" />
                            Published
                          </span>
                        )}
                        <span className="text-xs text-museum-500">
                          Resource Type: {record.resourceType?.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()) || 'Unknown'} ({record.assetCount || 0})
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </td>
              <td className="px-6 py-4 text-sm text-museum-900 w-36">
                <div>
                  <p className="font-medium">{record.date}</p>
                </div>
              </td>
              <td className="px-6 py-4 text-sm">
                <div className="space-y-1">
                  <p className="font-medium text-museum-900">{record.creator.name}</p>
                  <p className="text-museum-500 capitalize">{record.creator.role}</p>
                </div>
              </td>
              <td className="px-6 py-4 text-sm">
  <div className="flex flex-col items-center space-y-1">
    {record.source.repository && (
      <span className="text-museum-900 font-medium text-center">
        {typeof record.source.repository === 'string' ? record.source.repository : String(record.source.repository)}
      </span>
    )}
    {record.source.collection && (
      <span className="text-museum-600 text-xs text-center">
        {typeof record.source.collection === 'string' ? record.source.collection : String(record.source.collection)}
      </span>
    )}
    {record.documentId && (
      <span className="text-museum-500 text-xs text-center">{record.documentId}</span>
    )}
  </div>
</td>

              <td className="px-6 py-4">
  {(!record.assetCount || record.assetCount === 0) ? (
    <span className="text-sm font-medium text-museum-500">
      No Asset Available
    </span>
  ) : record.ocr_processing_status === "completed" ? 
  (
  record.aiConfidence === 0 && record.assetCount > 0 ? (
    <span className="text-sm font-medium text-museum-500">
      No Readable Text
    </span>
  ) : (
    <div className="flex items-center space-x-2">
      <div className="flex-1 bg-museum-200 rounded-full h-2">
        <div
          className={`h-2 rounded-full ${getConfidenceBarColor(record.aiConfidence)}`}
          style={{ width: `${record.aiConfidence}%` }}
        ></div>
      </div>
      <span
        className={`text-sm font-medium ${getConfidenceColor(record.aiConfidence)}`}
      >
        {record.aiConfidence}%
      </span>
    </div>
  )): (
    <span className="text-sm font-medium text-museum-500"> 
    {record.ocr_processing_status === "pending" && "Pending OCR"}
    {record.ocr_processing_status === "error" && "Error"}
    {record.ocr_processing_status === "no_assets_found" && "No Asset Available"}
    </span>
  ) }
</td>

              <td className="px-6 py-4">{getStatusBadge(record)}</td>
              <td className="px-6 py-4 text-sm text-museum-600">
                <div>
                  <p>{record.lastEdited?.time || 'Never'}</p>
                  {/* <p className="text-xs">{record.lastEdited?.user || '—'}</p> */}
                </div>
              </td>
            </tr>
          )})}
        </tbody>
      </table>
    </div>
  </section>
)

}

export default RecordsTable
