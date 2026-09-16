// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState, useEffect } from 'react'
import Header from '@/components/Header'
import BreadcrumbNav from '@/components/BreadcrumbNav'
import CollectionsHeader from '@/components/CollectionsHeader'
import CollectionStats from '@/components/CollectionStats'
import CollectionFilters from '@/components/CollectionFilters'
import CollectionsGrid from '@/components/CollectionsGrid'
// import RecentDocuments from '@/components/RecentDocuments'
// import WorkflowStatus from '@/components/WorkflowStatus'
// import SystemHealth from '@/components/SystemHealth'
import Footer from '@/components/Footer'
import Toast from '@/components/Toast'
import { apiService, ApiCollectionSummary } from '@/services/api'
import { CollectionSummary } from '@/types'

const CollectionsPage: React.FC = () => {
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  const [viewMode, setViewMode] = useState<'grid' | 'list' | 'table'>('grid')
  const [filters, setFilters] = useState({
    repository: '',
    collection: ''
  })
  const [collectionSummaries, setCollectionSummaries] = useState<CollectionSummary[]>([])
  const [isLoading, setIsLoading] = useState<boolean>(true)

  // Fetch collection summaries from API on mount
  useEffect(() => {
    const fetchCollectionSummaries = async () => {
      try {
        setIsLoading(true)
        const response = await apiService.getCollectionSummaries()

        // Map API response to frontend CollectionSummary format
        const summaries: CollectionSummary[] = response.summaries.map((summary: ApiCollectionSummary) => ({
          repository: summary.repository,
          collection: summary.collection,
          code: '', // Will be generated in CollectionsGrid
          color: '', // Will be generated in CollectionsGrid
          totalItems: summary.totalItems,
          pending: summary.pending,
          completionRate: summary.completionRate,
          completed: summary.completed,
          published: summary.published,
          avgOcrConfidence: summary.avgOcrConfidence ?? null,
        }))

        setCollectionSummaries(summaries)
      } catch (error) {
        console.error('Failed to fetch collection summaries:', error)
        // Fallback to empty array - will use static data in CollectionsGrid
        setCollectionSummaries([])
        setToast({
          type: 'error',
          message: 'Could not connect to API. Displaying sample data.'
        })
      } finally {
        setIsLoading(false)
      }
    }

    fetchCollectionSummaries()
  }, [])

  const handleToastClose = () => {
    setToast(null)
  }

  const handleSyncCollections = () => {
    setToast({ type: 'success', message: 'Collections synchronized successfully.' })
  }

  const handleFiltersChange = (newFilters: typeof filters) => {
    setFilters(newFilters)
  }

  const handleViewModeChange = (mode: 'grid' | 'list' | 'table') => {
    setViewMode(mode)
  }

  // Extract unique repositories and collections from summaries
  const uniqueRepositories = React.useMemo(() => {
    return Array.from(new Set(collectionSummaries.map(s => s.repository).filter(Boolean))).sort()
  }, [collectionSummaries])

  const uniqueCollections = React.useMemo(() => {
    return Array.from(new Set(collectionSummaries.map(s => s.collection).filter(Boolean))).sort()
  }, [collectionSummaries])

  // Calculate aggregate stats from collection summaries
  const aggregateStats = React.useMemo(() => {
    const totalItems = collectionSummaries.reduce((sum, c) => sum + c.totalItems, 0)
    const totalPending = collectionSummaries.reduce((sum, c) => sum + c.pending, 0)
    const totalCompleted = collectionSummaries.reduce((sum, c) => sum + c.completed, 0)
    const numberOfCollections = collectionSummaries.length

    return {
      totalItems,
      totalPending,
      totalCompleted,
      numberOfCollections
    }
  }, [collectionSummaries])

  return (
    <div className="h-full bg-museum-50">
      <Header
        currentPage="collections"
        onNotificationClick={() => { }}
        onHelpClick={() => { }}
        onUserMenuClick={() => { }}
      />

      {/* <BreadcrumbNav /> */}

      <CollectionsHeader
        onSyncCollections={handleSyncCollections}
      />

      <CollectionStats
        totalItems={aggregateStats.totalItems}
        totalPending={aggregateStats.totalPending}
        totalCompleted={aggregateStats.totalCompleted}
        numberOfCollections={aggregateStats.numberOfCollections}
      />

      <CollectionFilters
        filters={filters}
        viewMode={viewMode}
        onFiltersChange={handleFiltersChange}
        onViewModeChange={handleViewModeChange}
        repositories={uniqueRepositories}
        collections={uniqueCollections}
      />

      {isLoading ? (
        <div className="relative w-full h-full flex items-center justify-center bg-gray-50" style={{ minHeight: 'calc(100vh - 16rem)' }}>
          <div className="flex items-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-museum-600"></div>
            <span className="ml-4 text-museum-600">Loading collections...</span>
          </div>
        </div>
      ) : (
        <CollectionsGrid
          viewMode={viewMode}
          filters={filters}
          collectionSummaries={collectionSummaries}
        />
      )}

      {/* <RecentDocuments /> */}

      {/* <WorkflowStatus /> */}

      {/* <SystemHealth /> */}
      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={handleToastClose}
          />
        </div>
      )}
      {/* <Footer /> */}
    </div>

  )
}

export default CollectionsPage