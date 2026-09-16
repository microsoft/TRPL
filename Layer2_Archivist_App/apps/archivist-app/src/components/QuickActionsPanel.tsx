// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import { Download, RefreshCw, BarChart3, Settings } from 'lucide-react'

interface QuickAction {
  id: string
  title: string
  description: string
  icon: React.ReactNode
  iconBg: string
  hoverBorder: string
  hoverIconBg: string
}

const QuickActionsPanel: React.FC = () => {
  const actions: QuickAction[] = [
    {
      id: 'export',
      title: 'Export Queue Data',
      description: 'Download current queue as CSV or JSON',
      icon: <Download className="w-6 h-6 text-blue-600" />,
      iconBg: 'bg-blue-100',
      hoverBorder: 'hover:border-blue-300',
      hoverIconBg: 'group-hover:bg-blue-200'
    },
    {
      id: 'sync',
      title: 'Force Sync',
      description: 'Manually trigger data synchronization',
      icon: <RefreshCw className="w-6 h-6 text-green-600" />,
      iconBg: 'bg-green-100',
      hoverBorder: 'hover:border-green-300',
      hoverIconBg: 'group-hover:bg-green-200'
    },
    {
      id: 'report',
      title: 'Generate Report',
      description: 'Create comprehensive analytics report',
      icon: <BarChart3 className="w-6 h-6 text-purple-600" />,
      iconBg: 'bg-purple-100',
      hoverBorder: 'hover:border-purple-300',
      hoverIconBg: 'group-hover:bg-purple-200'
    },
    {
      id: 'maintenance',
      title: 'System Maintenance',
      description: 'Access maintenance and diagnostic tools',
      icon: <Settings className="w-6 h-6 text-red-600" />,
      iconBg: 'bg-red-100',
      hoverBorder: 'hover:border-red-300',
      hoverIconBg: 'group-hover:bg-red-200'
    }
  ]

  const handleActionClick = (actionId: string) => {
    console.log('Quick action clicked:', actionId)
    // In a real app, this would trigger the appropriate action
  }

  return (
    <section id="quick-actions-panel" className="bg-white px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h3 className="text-xl font-semibold text-museum-900 mb-2">Quick Actions</h3>
          <p className="text-museum-600">Common administrative tasks and system operations</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {actions.map((action) => (
            <button
              key={action.id}
              onClick={() => handleActionClick(action.id)}
              className={`bg-white border border-museum-200 rounded-xl p-6 hover:shadow-md transition-all duration-200 ${action.hoverBorder} group`}
            >
              <div className={`w-12 h-12 ${action.iconBg} rounded-lg flex items-center justify-center mb-4 ${action.hoverIconBg} transition-colors`}>
                {action.icon}
              </div>
              <h4 className="font-semibold text-museum-900 mb-2">{action.title}</h4>
              <p className="text-sm text-museum-500">{action.description}</p>
            </button>
          ))}
        </div>
      </div>
    </section>
  )
}

export default QuickActionsPanel
