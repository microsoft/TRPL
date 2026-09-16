// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'

const SystemHealth: React.FC = () => {
  const healthMetrics = [
    {
      title: 'AI Processing Engine',
      status: 'Healthy',
      statusColor: 'bg-green-100 text-green-800',
      metrics: [
        { label: 'Queue Length', value: '3,892 items' },
        { label: 'Processing Rate', value: '147/hour' },
        { label: 'Avg. Confidence', value: '78.3%' }
      ]
    },
    {
      title: 'Data Synchronization',
      status: 'Active',
      statusColor: 'bg-green-100 text-green-800',
      metrics: [
        { label: 'Active Connections', value: '6 of 7' },
        { label: 'Last Full Sync', value: '2 hours ago' },
        { label: 'Sync Success Rate', value: '99.7%' }
      ]
    },
    {
      title: 'Storage & Performance',
      status: 'Optimal',
      statusColor: 'bg-green-100 text-green-800',
      metrics: [
        { label: 'Storage Used', value: '2.4TB / 5TB' },
        { label: 'Avg. Response Time', value: '847ms' },
        { label: 'Uptime', value: '99.98%' }
      ]
    }
  ]

  return (
    <section id="system-health" className="bg-white border-t border-museum-200 px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-xl font-semibold text-museum-900">System Health & Monitoring</h3>
          <div className="flex items-center space-x-2 text-sm text-green-600">
            <div className="w-2 h-2 bg-green-500 rounded-full"></div>
            <span>All systems operational</span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {healthMetrics.map((metric, index) => (
            <div key={index} className="bg-museum-50 rounded-lg p-6 border border-museum-200">
              <div className="flex items-center justify-between mb-4">
                <h4 className="font-medium text-museum-900">{metric.title}</h4>
                <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${metric.statusColor}`}>
                  <div className="w-2 h-2 bg-current rounded-full mr-1"></div>
                  {metric.status}
                </span>
              </div>
              <div className="space-y-2 text-sm">
                {metric.metrics.map((item, itemIndex) => (
                  <div key={itemIndex} className="flex justify-between">
                    <span className="text-museum-600">{item.label}</span>
                    <span className="font-medium text-museum-900">{item.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default SystemHealth
