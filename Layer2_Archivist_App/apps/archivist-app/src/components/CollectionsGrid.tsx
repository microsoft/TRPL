'use client'

import React from 'react'
import { useNavigate } from 'react-router-dom'
import { Landmark, GraduationCap, Book, School, Scroll, Building2, Home, CheckCircle, AlertTriangle, Clock } from 'lucide-react'
import { CollectionSummary } from '@/types'
import { equalsIgnoreCase } from '@/utils/textCompare'

interface Collection {
  // id: string
  name: string
  description: string
  icon: React.ReactNode
  iconBg: string
  status: 'active' | 'syncing' | 'maintenance'
  statusColor: string
  totalItems: number
  pending: number
  avgOcrConfidence: number | null
  completionRate: number
  recentActivity: {
    validated: number
    flagged: number
    lastSync: string
    syncing?: number
  }
}

interface CollectionsGridProps {
  viewMode: 'grid' | 'list' | 'table'
  filters: {
    repository: string
    collection: string
  }
  collectionSummaries: CollectionSummary[]
}

const CollectionsGrid: React.FC<CollectionsGridProps> = ({ viewMode, filters, collectionSummaries }) => {
  const navigate = useNavigate()

  // Handler for navigating to home page with filters
  const handleReviewQueueClick = (repository: string, collection: string) => {
    // Navigate to home and send filters both as location state and as URL query
    // so the Home page can read them from either place (state for SPA navigation,
    // query params for bookmarking / direct links).
    const searchParams = new URLSearchParams()
    if (repository) searchParams.set('repository', repository)
    if (collection) searchParams.set('collection', collection)

    const searchString = searchParams.toString()
    const url = searchString ? `/home?${searchString}` : '/home'

    // Use a string path for navigate to avoid any inconsistencies with object form
    navigate(url, {
      state: {
        filters: {
          repository,
          collection
        }
      }
    })
  }
  
  // Helper function to get icon for repository
  const getRepositoryIcon = (repository: string) => {
    const lowerRepo = repository.toLowerCase()
    if (lowerRepo.includes('library of congress') || lowerRepo.includes('loc')) {
      return { icon: <Landmark className="w-6 h-6 text-blue-600" />, bg: 'bg-blue-100' }
    } else if (lowerRepo.includes('harvard')) {
      return { icon: <GraduationCap className="w-6 h-6 text-red-600" />, bg: 'bg-red-100' }
    } else if (lowerRepo.includes('morris')) {
      return { icon: <Book className="w-6 h-6 text-green-600" />, bg: 'bg-green-100' }
    } else if (lowerRepo.includes('yale')) {
      return { icon: <School className="w-6 h-6 text-blue-600" />, bg: 'bg-blue-100' }
    } else if (lowerRepo.includes('columbia')) {
      return { icon: <Scroll className="w-6 h-6 text-indigo-600" />, bg: 'bg-indigo-100' }
    } else if (lowerRepo.includes('national archives') || lowerRepo.includes('nara')) {
      return { icon: <Building2 className="w-6 h-6 text-purple-600" />, bg: 'bg-purple-100' }
    } else if (lowerRepo.includes('roosevelt')) {
      return { icon: <Home className="w-6 h-6 text-amber-600" />, bg: 'bg-amber-100' }
    }
    return { icon: <Book className="w-6 h-6 text-gray-600" />, bg: 'bg-gray-100' }
  }
  
  // Helper to generate code from name
  const generateCode = (name: string) => {
    return name.split(' ').map(word => word[0]).join('').substring(0, 3).toUpperCase()
  }
  
  // Map collection summaries to Collection format
  const collections: Collection[] = collectionSummaries.map((summary) => {
    const { icon, bg } = getRepositoryIcon(summary.repository)
    const code = summary.code || generateCode(summary.collection || summary.repository)
    // const id = `${code.toLowerCase()}-${summary.repository.toLowerCase().replace(/\s+/g, '-')}`
    
    // Determine status based on completion rate (you may keep this logic or adjust)
    let status: 'active' | 'syncing' | 'maintenance' = 'active'
    let statusColor = 'bg-green-100 text-green-800'
    
    //completionRate
    const total = summary.totalItems || 0
    const pending = summary.pending || 0
    const completionRate = total > 0 
    ? ((total - pending) / total) * 100
    : 0

    if (total > 0 && (pending / total) > 0.15) {
      // original heuristic used completionRate < 85 -> syncing; adjust to pending ratio > 15%
      status = 'syncing'
      statusColor = 'bg-amber-100 text-amber-800'
    }

    return {
      // id,
      name: summary.repository,
      description: summary.collection,
      icon,
      iconBg: bg,
      status,
      statusColor,
      totalItems: total,
      pending: pending,
      avgOcrConfidence:
        summary.avgOcrConfidence !== undefined && summary.avgOcrConfidence !== null
          ? summary.avgOcrConfidence
          : null,
      completionRate, // now pending/total as percentage
      recentActivity: {
        validated: summary.totalItems - summary.pending,
        flagged: summary.pending,
        lastSync: 'Just now'
      }
    }
  })
  
  // Fallback to static data if no collections generated
  const staticCollections: Collection[] = [
    {
      // id: 'loc',
      name: 'Library of Congress',
      description: 'Primary presidential collection',
      icon: <Landmark className="w-6 h-6 text-blue-600" />,
      iconBg: 'bg-blue-100',
      status: 'active',
      statusColor: 'bg-green-100 text-green-800',
      totalItems: 47329,
      pending: 2847,
      avgOcrConfidence: 88.5,
      completionRate: 94.0,
      recentActivity: {
        validated: 142,
        flagged: 23,
        lastSync: '2 hours ago'
      }
    },
    {
      // id: 'harvard',
      name: 'Harvard University',
      description: 'Houghton Library collection',
      icon: <GraduationCap className="w-6 h-6 text-red-600" />,
      iconBg: 'bg-red-100',
      status: 'active',
      statusColor: 'bg-green-100 text-green-800',
      totalItems: 23847,
      pending: 1923,
      avgOcrConfidence: 90.0,
      completionRate: 91.9,
      recentActivity: {
        validated: 87,
        flagged: 15,
        lastSync: '4 hours ago'
      }
    },
    {
      // id: 'morris',
      name: 'Morris Library',
      description: 'University of Delaware',
      icon: <Book className="w-6 h-6 text-green-600" />,
      iconBg: 'bg-green-100',
      status: 'syncing',
      statusColor: 'bg-amber-100 text-amber-800',
      totalItems: 18492,
      pending: 2134,
      avgOcrConfidence: null,
      completionRate: 88.5,
      recentActivity: {
        validated: 63,
        flagged: 41,
        lastSync: 'Syncing: 847 items remaining',
        syncing: 847
      }
    },
    {
      // id: 'yale',
      name: 'Yale University',
      description: 'Manuscripts & Archives',
      icon: <School className="w-6 h-6 text-blue-600" />,
      iconBg: 'bg-blue-100',
      status: 'active',
      statusColor: 'bg-green-100 text-green-800',
      totalItems: 14726,
      pending: 892,
      avgOcrConfidence: 92.3,
      completionRate: 93.9,
      recentActivity: {
        validated: 54,
        flagged: 8,
        lastSync: '1 hour ago'
      }
    },
    {
      // id: 'columbia',
      name: 'Columbia University',
      description: 'Rare Book & Manuscript',
      icon: <Scroll className="w-6 h-6 text-indigo-600" />,
      iconBg: 'bg-indigo-100',
      status: 'active',
      statusColor: 'bg-green-100 text-green-800',
      totalItems: 11203,
      pending: 447,
      avgOcrConfidence: 95.0,
      completionRate: 96.0,
      recentActivity: {
        validated: 31,
        flagged: 3,
        lastSync: '3 hours ago'
      }
    },
    {
      // id: 'nara',
      name: 'National Archives',
      description: 'Presidential records',
      icon: <Building2 className="w-6 h-6 text-purple-600" />,
      iconBg: 'bg-purple-100',
      status: 'maintenance',
      statusColor: 'bg-red-100 text-red-800',
      totalItems: 8934,
      pending: 186,
      avgOcrConfidence: 96.2,
      completionRate: 97.9,
      recentActivity: {
        validated: 0,
        flagged: 0,
        lastSync: '12 hours ago'
      }
    },
    {
      // id: 'roosevelt',
      name: 'Roosevelt House',
      description: 'Personal family collection',
      icon: <Home className="w-6 h-6 text-amber-600" />,
      iconBg: 'bg-amber-100',
      status: 'active',
      statusColor: 'bg-green-100 text-green-800',
      totalItems: 2907,
      pending: 0,
      avgOcrConfidence: 99.0,
      completionRate: 100,
      recentActivity: {
        validated: 0,
        flagged: 0,
        lastSync: '30 minutes ago'
      }
    }
  ]

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'active':
        return <CheckCircle className="w-2 h-2" />
      case 'syncing':
        return <Clock className="w-2 h-2" />
      case 'maintenance':
        return <AlertTriangle className="w-2 h-2" />
      default:
        return <CheckCircle className="w-2 h-2" />
    }
  }

  // Use dynamic collections if available, otherwise use static fallback
  const displayCollections = collections.length > 0 ? collections : staticCollections

  const filteredCollections = displayCollections.filter((collection) => {
    const matchesRepository =
      filters.repository === '' || equalsIgnoreCase(collection.name, filters.repository)

    const matchesCollection =
      filters.collection === '' || equalsIgnoreCase(collection.description, filters.collection)

    return matchesRepository && matchesCollection
  })

  const getGridClasses = () => {
    switch (viewMode) {
      case 'grid':
        return 'grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-8'
      case 'list':
        return 'grid grid-cols-1 gap-4'
      case 'table':
        return 'grid grid-cols-1'
      default:
        return 'grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-8'
    }
  }

  if (viewMode === 'table') {
    return (
      <section id="collections-grid" className="px-6 py-8">
        <div className="max-w-7xl mx-auto">
          <div className="bg-museum-50 rounded-xl border border-museum-200 overflow-hidden">
            <table className="w-full">
              <thead className="bg-museum-50 border-b border-museum-200">
                <tr>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Collection</th>
                  {/* <th className="px-6 py-4 text-left text-sm font-semibold text-museum-900">Status</th> */}
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Total Items</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Pending Review</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Avg OCR</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Completion Rate</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-museum-200">
                {filteredCollections.map((collection) => (
                  <tr className="hover:bg-museum-50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-start space-x-1">
                      {/* <div className="flex items-center space-x-3 justify-start max-w-md mx-auto"> */}
                        <div className={`w-12 h-12 ${collection.iconBg} bg-museum-100 rounded-lg flex items-center justify-center shrink-0`}>
                            {collection.icon}
                        </div>
                        <div className="min-w-0 flex-1">
                            <h3 className="font-semibold text-museum-900">{collection.name}</h3>
                            <p className="text-sm text-museum-500">{collection.description}</p>
                        </div>
                      {/* </div> */}
                      </div>
                    </td>
                    {/* <td className="px-6 py-4">
                      <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${collection.statusColor}`}>
                        <div className="w-2 h-2 bg-current rounded-full mr-1"></div>
                        {collection.status.charAt(0).toUpperCase() + collection.status.slice(1)}
                      </span>
                    </td> */}
                    <td className="px-6 py-4 text-sm text-center text-museum-900">{collection.totalItems.toLocaleString()}</td>
                    <td className="px-6 py-4 text-sm text-center text-amber-600">{collection.pending.toLocaleString()}</td>
                    <td className="px-6 py-4 text-sm text-center text-indigo-700">
                      {collection.avgOcrConfidence != null ? `${collection.avgOcrConfidence.toFixed(1)}%` : '—'}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center space-x-2">
                        <div className="w-16 bg-museum-200 rounded-full h-2">
                          <div 
                            className="bg-green-500 h-2 rounded-full" 
                            style={{ width: `${collection.completionRate}%` }}
                          ></div>
                        </div>
                        <span className="text-sm font-medium text-green-600">{collection.completionRate.toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <button 
                        className="px-3 py-1.5  bg-museum-accent text-museum-50  hover:bg-museum-700 transition-colors flex items-center space-x-1.5 text-sm"
                        onClick={() => handleReviewQueueClick(collection.name, collection.description)}
                      >
                        Review Queue
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section id="collections-grid" className="px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className={getGridClasses()}>
          {filteredCollections.map((collection) => (
            <div className="bg-white rounded-xl border border-museum-200 p-6 hover:shadow-lg transition-all duration-300 hover:-translate-y-1">
              <div className="flex items-center space-x-3 mb-4">
                <div className={`w-12 h-12 ${collection.iconBg} rounded-lg flex items-center justify-center`}>
                  {collection.icon}
                </div>
                <div>
                  <h3 className="font-semibold text-museum-900">{collection.name}</h3>
                  <p className="text-sm text-museum-500">{collection.description}</p>
                </div>
              </div>
              
              <div className="space-y-3 mb-6">
                <div className="flex justify-between items-center">
                  <span className="text-sm text-museum-600">Total Items</span>
                  <span className="font-medium text-museum-900">{collection.totalItems.toLocaleString()}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-sm text-museum-600">Pending Review</span>
                  <span className="font-medium text-amber-600">{collection.pending.toLocaleString()}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-sm text-museum-600">Avg OCR</span>
                  <span className="font-medium text-indigo-700">
                    {collection.avgOcrConfidence != null ? `${collection.avgOcrConfidence.toFixed(1)}%` : '—'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-sm text-museum-600">Completion Rate</span>
                  <span className="font-medium text-green-600">{(collection.completionRate).toFixed(1)}%</span>
                </div>
                <div className="w-full bg-museum-200 rounded-full h-2">
                  <div 
                    className="bg-green-500 h-2 rounded-full" 
                    style={{ width: `${collection.completionRate.toFixed(1)}%` }}
                  ></div>
                </div>
              </div>

              <div className="space-y-3 mb-6">
                <h4 className="text-sm font-medium text-museum-900">Recent Activity</h4>
                <div className="text-xs text-museum-600 space-y-1">
                  <div className="flex items-center space-x-2">
                    <div className="w-2 h-2 bg-green-500 rounded-full"></div>
                    <span>{collection.recentActivity.validated} items validated today</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <div className="w-2 h-2 bg-amber-500 rounded-full"></div>
                    <span>{collection.recentActivity.flagged} items flagged for review</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <div className={`w-2 h-2 bg-blue-500 rounded-full ${collection.status === 'syncing' ? 'animate-pulse' : ''}`}></div>
                    <span>{collection.recentActivity.lastSync}</span>
                  </div>
                </div>
              </div>

              <div className="flex justify-end pt-4 border-t border-museum-100">
                <button 
                  className={`px-3 py-1.5 bg-museum-accent text-white hover:bg-museum-700 transition-colors flex items-center space-x-1.5 text-sm  ${
                    collection.status === 'maintenance' 
                      ? 'bg-museum-300 text-museum-500 cursor-not-allowed' 
                      : collection.completionRate === 100
                      ? 'bg-green-600 hover:bg-green-700'
                      : 'bg-museum-600 hover:bg-museum-700'
                  }`}
                  disabled={collection.status === 'maintenance'}
                  onClick={() => handleReviewQueueClick(collection.name, collection.description)}
                >
                  {collection.completionRate === 100 ? 'Browse Collection' : 'Review Queue'}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default CollectionsGrid
