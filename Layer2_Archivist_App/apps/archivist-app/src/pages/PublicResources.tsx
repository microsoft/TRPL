// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BookOpen,
  Calendar,
  FileArchive,
  ArrowRight,
  Loader2,
  Database,
} from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import { apiService } from '@/services/api'
import { useAuth } from '@/contexts/AuthContext'

// =============================================================================
// Resource Card Definitions
// =============================================================================

const resources = [
  {
    id: 'cyclopedia',
    title: 'Theodore Roosevelt Cyclopedia',
    description:
      'Published by the TRA, in the public domain — significant quotes and speeches of Theodore Roosevelt organized by subject.',
    icon: BookOpen,
    iconBg: 'bg-blue-100',
    iconColor: 'text-blue-600',
    path: '/digital-resources/cyclopedia',
    cosmosSource: 'tr-cyclopedia',
  },
  {
    id: 'moore-chronology',
    title: 'The Moore Chronology',
    description:
      'Quite literally every single day of TR\'s life digitized and available — ten chronological volumes from the Theodore Roosevelt Center.',
    icon: Calendar,
    iconBg: 'bg-amber-100',
    iconColor: 'text-amber-600',
    path: '/digital-resources/moore-chronology',
    cosmosSource: 'moore-chronology',
  },
  {
    id: 'genealogy-papers',
    title: 'Digitized Genealogy & Papers by the TRA',
    description:
      'A chronology of every day of TR\'s life compiled by Wallace Dailey, intended to be widely available. Physical copies pending full digitization.',
    icon: FileArchive,
    iconBg: 'bg-emerald-100',
    iconColor: 'text-emerald-600',
    path: '/digital-resources/genealogy-papers',
    cosmosSource: 'genealogy-papers',
  },
]

// =============================================================================
// Main Component
// =============================================================================

const PublicResourcesPage: React.FC = () => {
  const navigate = useNavigate()
  const { hasAnyRole } = useAuth()
  const [sourceCounts, setSourceCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)

  const fetchCounts = useCallback(async () => {
    try {
      const summary = await apiService.getDigitalItemsSummary()
      setSourceCounts(summary)
    } catch {
      // Silently fail
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchCounts()
  }, [fetchCounts])

  const hasData = (cosmosSource: string) => (sourceCounts[cosmosSource] || 0) > 0
  const hasAnyData = resources.some(r => hasData(r.cosmosSource))

  return (
    <div className="min-h-screen bg-gray-50">
      <PageHeader
        title="Public Resources"
        subtitle="Specialized reference materials and digitized collections"
      />

      <div className="max-w-7xl mx-auto px-6 py-8">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            <span className="ml-2 text-gray-500">Loading resources…</span>
          </div>
        ) : !hasAnyData ? (
          <div className="text-center py-16">
            <Database className="w-12 h-12 mx-auto mb-4 text-gray-300" />
            <h3 className="text-lg font-semibold text-gray-700 mb-2">No public resources available yet</h3>
            {hasAnyRole ? (
              <p className="text-sm text-gray-500 max-w-md mx-auto">
                Public resource collections have not been ingested. Use the <strong>Public Ingestion</strong> tool under Tools in the sidebar to populate data.
              </p>
            ) : (
              <p className="text-sm text-gray-500 max-w-md mx-auto">
                Public resource collections have not been ingested yet. Your account currently has read-only access.
              </p>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {resources.map((resource) => {
              const Icon = resource.icon
              const available = hasData(resource.cosmosSource)
              const count = sourceCounts[resource.cosmosSource] || 0
              return (
                <div
                  key={resource.id}
                  className={`bg-white rounded-xl border border-gray-200 shadow-sm flex flex-col overflow-hidden transition-all ${
                    available
                      ? 'cursor-pointer group hover:shadow-lg'
                      : 'opacity-50 cursor-default'
                  }`}
                  onClick={() => available && navigate(resource.path)}
                >
                  <div className="p-6 flex-1">
                    <div className="flex items-center justify-between mb-4">
                      <div className={`w-14 h-14 ${resource.iconBg} rounded-xl flex items-center justify-center`}>
                        <Icon className={`w-7 h-7 ${resource.iconColor}`} />
                      </div>
                      {available ? (
                        <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-gray-600 group-hover:translate-x-1 transition-all" />
                      ) : (
                        <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">No data</span>
                      )}
                    </div>
                    <h3 className={`text-lg font-semibold mb-2 transition-colors ${
                      available ? 'text-gray-900 group-hover:text-museum-accent' : 'text-gray-500'
                    }`}>
                      {resource.title}
                    </h3>
                    <p className="text-sm text-gray-500 leading-relaxed">
                      {resource.description}
                    </p>
                    <div className="mt-3 text-xs text-gray-400">
                      {count > 0 ? `${count} items` : 'Not yet ingested'}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

export default PublicResourcesPage
