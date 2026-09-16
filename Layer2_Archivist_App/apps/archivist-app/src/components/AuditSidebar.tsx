// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import { X, Clock, Edit, AlertTriangle, CheckCircle, Save } from 'lucide-react'

interface AuditSidebarProps {
  isOpen: boolean
  onClose: () => void
  documentId: string
}

interface AuditEntry {
  id: string
  type: 'review_started' | 'transcription_modified' | 'severe_deviation_flagged' | 'metadata_updated' | 'auto_saved'
  title: string
  description: string
  timestamp: string
  user?: string
  details?: string
}

const AuditSidebar: React.FC<AuditSidebarProps> = ({
  isOpen,
  onClose
}) => {
  // Mock audit trail data
  const auditEntries: AuditEntry[] = [
    {
      id: '1',
      type: 'review_started',
      title: 'Review Started',
      description: 'Archivist A began reviewing document',
      timestamp: '23 min ago',
      user: 'archivist.a@example.com'
    },
    {
      id: '2',
      type: 'transcription_modified',
      title: 'Transcription Modified',
      description: 'Corrected 4 transcription errors, added missing paragraph',
      timestamp: '21 min ago'
    },
    {
      id: '3',
      type: 'severe_deviation_flagged',
      title: 'Severe Deviation Flagged',
      description: 'Marked document as having severe AI transcription deviations',
      timestamp: '18 min ago',
      details: 'Reason: Multiple transcription errors and missing content'
    },
    {
      id: '4',
      type: 'metadata_updated',
      title: 'Metadata Updated',
      description: 'Added topic: Foreign Policy, updated sentiment to Neutral',
      timestamp: '15 min ago'
    },
    {
      id: '5',
      type: 'auto_saved',
      title: 'Auto-saved',
      description: 'All changes automatically saved',
      timestamp: '2 min ago'
    }
  ]

  const getEntryIcon = (type: AuditEntry['type']) => {
    switch (type) {
      case 'review_started':
        return <CheckCircle className="w-4 h-4 text-green-500" />
      case 'transcription_modified':
        return <Edit className="w-4 h-4 text-blue-500" />
      case 'severe_deviation_flagged':
        return <AlertTriangle className="w-4 h-4 text-amber-500" />
      case 'metadata_updated':
        return <Edit className="w-4 h-4 text-purple-500" />
      case 'auto_saved':
        return <Save className="w-4 h-4 text-green-500" />
      default:
        return <Clock className="w-4 h-4 text-museum-500" />
    }
  }

  const getEntryBorderColor = (type: AuditEntry['type']) => {
    switch (type) {
      case 'review_started':
        return 'border-green-500'
      case 'transcription_modified':
        return 'border-blue-500'
      case 'severe_deviation_flagged':
        return 'border-amber-500'
      case 'metadata_updated':
        return 'border-purple-500'
      case 'auto_saved':
        return 'border-green-500'
      default:
        return 'border-museum-300'
    }
  }

  if (!isOpen) return null

  return (
    <aside className="fixed right-0 top-0 h-full w-80 bg-white border-l border-museum-200 shadow-xl z-50">
      <div className="h-full flex flex-col">
        <div className="flex items-center justify-between p-6 border-b border-museum-200">
          <h3 className="text-lg font-semibold text-museum-900">Audit Trail</h3>
          <button 
            className="p-2 text-museum-500 hover:text-museum-700 hover:bg-museum-100 rounded-lg transition-colors"
            onClick={onClose}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          <div className="p-6 space-y-4">
            {auditEntries.map((entry) => (
              <div key={entry.id} className={`border-l-4 ${getEntryBorderColor(entry.type)} pl-4`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium text-museum-900">{entry.title}</span>
                  <span className="text-xs text-museum-500">{entry.timestamp}</span>
                </div>
                <p className="text-sm text-museum-600">{entry.description}</p>
                {entry.user && (
                  <p className="text-xs text-museum-500">User: {entry.user}</p>
                )}
                {entry.details && (
                  <p className="text-xs text-museum-500">{entry.details}</p>
                )}
                {entry.type === 'transcription_modified' && (
                  <button className="text-xs text-blue-600 hover:text-blue-800 underline mt-1">
                    View changes
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </aside>
  )
}

export default AuditSidebar
