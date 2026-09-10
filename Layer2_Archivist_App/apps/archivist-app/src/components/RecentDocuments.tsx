'use client'

import React from 'react'
import { CheckCircle, AlertTriangle, ArrowRight } from 'lucide-react'

interface RecentDocument {
  id: string
  title: string
  date: string
  source: string
  type: string
  status: 'validated' | 'flagged'
  reviewer: string
  processed: string
}

const RecentDocuments: React.FC = () => {
  const documents: RecentDocument[] = [
    {
      id: '1',
      title: 'Letter to John Hay regarding Panama Canal',
      date: 'November 15, 1903',
      source: 'Library of Congress',
      type: 'Correspondence',
      status: 'validated',
      reviewer: 'Archivist A',
      processed: '2 hours ago'
    },
    {
      id: '2',
      title: 'Speech on Conservation Policy',
      date: 'March 22, 1905',
      source: 'Harvard University',
      type: 'Speech',
      status: 'validated',
      reviewer: 'Archivist E',
      processed: '4 hours ago'
    },
    {
      id: '3',
      title: 'Diary Entry - African Safari',
      date: 'April 7, 1910',
      source: 'Yale University',
      type: 'Diary',
      status: 'flagged',
      reviewer: 'Archivist F',
      processed: '1 day ago'
    },
    {
      id: '4',
      title: 'Official Statement on Trust Regulation',
      date: 'August 14, 1902',
      source: 'Columbia University',
      type: 'Official Document',
      status: 'validated',
      reviewer: 'Archivist G',
      processed: '1 day ago'
    },
    {
      id: '5',
      title: 'Family Photograph - Sagamore Hill',
      date: 'Summer 1904',
      source: 'Roosevelt House',
      type: 'Photograph',
      status: 'validated',
      reviewer: 'Archivist A',
      processed: '2 days ago'
    }
  ]

  

  return (
    <section id="recent-documents" className="bg-white border-t border-museum-200 px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-xl font-semibold text-museum-900">Recently Processed Documents</h3>
          <button className="text-sm text-museum-600 hover:text-museum-800 transition-colors flex items-center space-x-1">
            <span>View All</span>
            <ArrowRight className="w-3 h-3" />
          </button>
        </div>

       
      </div>
    </section>
  )
}

export default RecentDocuments
