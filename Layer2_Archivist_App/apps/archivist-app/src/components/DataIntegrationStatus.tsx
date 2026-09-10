'use client'

import React from 'react'
import { Database, Search, Server, Shield, CheckCircle, AlertCircle } from 'lucide-react'

interface IntegrationStatus {
  id: string
  name: string
  description: string
  status: 'online' | 'degraded' | 'offline'
  icon: React.ReactNode
  iconBg: string
  lastSync: string
  items: string
  latency: string
  latencyColor: string
}

const DataIntegrationStatus: React.FC = () => {
  const integrations: IntegrationStatus[] = [
    {
      id: 'cosmos',
      name: 'Cosmos DB',
      description: 'Primary document store',
      status: 'online',
      icon: <Database className="w-6 h-6 text-blue-600" />,
      iconBg: 'bg-blue-100',
      lastSync: '2 min ago',
      items: '1,589',
      latency: '12ms',
      latencyColor: 'text-green-600'
    },
    {
      id: 'search',
      name: 'Azure AI Search',
      description: 'Search index service',
      status: 'online',
      icon: <Search className="w-6 h-6 text-purple-600" />,
      iconBg: 'bg-purple-100',
      lastSync: '5 min ago',
      items: '1,587',
      latency: '45ms',
      latencyColor: 'text-green-600'
    },
    {
      id: 'content_source',
      name: 'Content Source Adapter',
      description: 'Digital asset management',
      status: 'degraded',
      icon: <Server className="w-6 h-6 text-green-600" />,
      iconBg: 'bg-green-100',
      lastSync: '1 hour ago',
      items: '12 pending',
      latency: '850ms',
      latencyColor: 'text-yellow-600'
    },
    {
      id: 'entra',
      name: 'Entra ID',
      description: 'Authentication service',
      status: 'online',
      icon: <Shield className="w-6 h-6 text-red-600" />,
      iconBg: 'bg-red-100',
      lastSync: 'Active',
      items: '8 users',
      latency: '120ms',
      latencyColor: 'text-green-600'
    }
  ]

  const getStatusBadge = (status: string) => {
    const statusConfig = {
      online: {
        bg: 'bg-green-100',
        text: 'text-green-800',
        icon: <CheckCircle className="w-3 h-3 mr-1" />
      },
      degraded: {
        bg: 'bg-yellow-100',
        text: 'text-yellow-800',
        icon: <AlertCircle className="w-3 h-3 mr-1" />
      },
      offline: {
        bg: 'bg-red-100',
        text: 'text-red-800',
        icon: <AlertCircle className="w-3 h-3 mr-1" />
      }
    }

    const config = statusConfig[status as keyof typeof statusConfig] || statusConfig.offline

    return (
      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${config.bg} ${config.text}`}>
        {config.icon}
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
    )
  }

  return (
    <section id="integration-status" className="bg-white px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h3 className="text-xl font-semibold text-museum-900 mb-2">Data Integration Status</h3>
          <p className="text-museum-600">Monitor connections and sync status with external systems</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {integrations.map((integration) => (
            <div key={integration.id} className="bg-white rounded-xl shadow-sm border border-museum-200 p-6">
              <div className="flex items-center justify-between mb-4">
                <div className={`w-12 h-12 ${integration.iconBg} rounded-lg flex items-center justify-center`}>
                  {integration.icon}
                </div>
                {getStatusBadge(integration.status)}
              </div>
              <div className="mb-4">
                <h4 className="font-semibold text-museum-900">{integration.name}</h4>
                <p className="text-sm text-museum-500">{integration.description}</p>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-museum-600">Last Sync:</span>
                  <span className="text-museum-900">{integration.lastSync}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-museum-600">Items:</span>
                  <span className="text-museum-900">{integration.items}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-museum-600">Latency:</span>
                  <span className={integration.latencyColor}>{integration.latency}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default DataIntegrationStatus
