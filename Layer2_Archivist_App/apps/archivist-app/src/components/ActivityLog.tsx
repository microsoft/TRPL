'use client'

import React from 'react'
import { Filter, Download } from 'lucide-react'

interface ActivityItem {
  id: string
  title: string
  description: string
  timestamp: string
  type: 'system' | 'config' | 'maintenance' | 'error' | 'security' | 'export'
  user: string
  color: string
}

const ActivityLog: React.FC = () => {
  const activities: ActivityItem[] = [
    {
      id: '1',
      title: 'Vocabulary updated',
      description: 'Archivist A added "conservation_photography" to Document Topics vocabulary',
      timestamp: '2 minutes ago',
      type: 'system',
      user: 'archivist.a@example.com',
      color: 'bg-green-500'
    },
    {
      id: '2',
      title: 'Configuration changed',
      description: 'AI confidence threshold for severe deviations updated from 25% to 30%',
      timestamp: '15 minutes ago',
      type: 'config',
      user: 'admin@example.com',
      color: 'bg-blue-500'
    },
    {
      id: '3',
      title: 'System maintenance',
      description: 'Scheduled index rebuild completed for Azure AI Search service',
      timestamp: '1 hour ago',
      type: 'maintenance',
      user: 'system',
      color: 'bg-amber-500'
    },
    {
      id: '4',
      title: 'Error detected',
      description: 'Content export status update failed for 3 items, retrying in background queue',
      timestamp: '2 hours ago',
      type: 'error',
      user: 'system',
      color: 'bg-red-500'
    },
    {
      id: '5',
      title: 'User access granted',
      description: 'Archivist C granted admin role access for vocabulary management',
      timestamp: '3 hours ago',
      type: 'security',
      user: 'archivist.d@example.com',
      color: 'bg-purple-500'
    },
    {
      id: '6',
      title: 'Bulk operation completed',
      description: 'Exported 247 pending items to CSV for external review process',
      timestamp: '4 hours ago',
      type: 'export',
      user: 'archivist.b@example.com',
      color: 'bg-green-500'
    }
  ]

  const getTypeBadge = (type: string) => {
    const typeConfig = {
      system: { bg: 'bg-green-100', text: 'text-green-800', label: 'System' },
      config: { bg: 'bg-blue-100', text: 'text-blue-800', label: 'Config' },
      maintenance: { bg: 'bg-amber-100', text: 'text-amber-800', label: 'Maintenance' },
      error: { bg: 'bg-red-100', text: 'text-red-800', label: 'Error' },
      security: { bg: 'bg-purple-100', text: 'text-purple-800', label: 'Security' },
      export: { bg: 'bg-green-100', text: 'text-green-800', label: 'Export' }
    }

    const config = typeConfig[type as keyof typeof typeConfig] || typeConfig.system

    return (
      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs ${config.bg} ${config.text}`}>
        {config.label}
      </span>
    )
  }

  return (
    <section id="activity-log" className="bg-museum-50 px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xl font-semibold text-museum-900 mb-2">System Activity Log</h3>
              <p className="text-museum-600">Recent administrative actions and system events</p>
            </div>
            <div className="flex items-center space-x-2">
              <button className="px-4 py-2 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors flex items-center space-x-2">
                <Filter className="w-4 h-4" />
                <span>Filter</span>
              </button>
              <button className="px-4 py-2 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors flex items-center space-x-2">
                <Download className="w-4 h-4" />
                <span>Export</span>
              </button>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-museum-200">
          <div className="p-6">
            <div className="space-y-6">
              {activities.map((activity) => (
                <div key={activity.id} className="flex items-start space-x-4">
                  <div className={`w-2 h-2 ${activity.color} rounded-full mt-3`}></div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-1">
                      <p className="text-sm font-medium text-museum-900">{activity.title}</p>
                      <span className="text-xs text-museum-500">{activity.timestamp}</span>
                    </div>
                    <p className="text-sm text-museum-600">{activity.description}</p>
                    <div className="flex items-center space-x-2 mt-2">
                      {getTypeBadge(activity.type)}
                      <span className="text-xs text-museum-500">•</span>
                      <span className="text-xs text-museum-500">{activity.user}</span>
                      {activity.type === 'error' && (
                        <>
                          <span className="text-xs text-museum-500">•</span>
                          <button className="text-xs text-blue-600 hover:underline">View Details</button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="px-6 py-4 border-t border-museum-200 bg-museum-50">
            <div className="flex items-center justify-between">
              <span className="text-sm text-museum-600">Showing last 6 activities</span>
              <button className="text-sm text-blue-600 hover:text-blue-800 font-medium">View All Activity</button>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default ActivityLog
