// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import { Clock, CheckCircle, AlertTriangle, Users, TrendingUp } from 'lucide-react'

const MetricsDashboard: React.FC = () => {
  const kpiData = [
    {
      title: 'Items in Queue',
      value: '247',
      subtitle: 'Items in Queue',
      change: '+12%',
      changeType: 'increase',
      period: 'vs yesterday',
      icon: <Clock className="w-6 h-6 text-white" />,
      bgColor: 'from-blue-50 to-blue-100',
      borderColor: 'border-blue-200',
      iconBg: 'bg-blue-600',
      textColor: 'text-blue-900',
      subtitleColor: 'text-blue-700',
      changeColor: 'text-red-600',
      periodColor: 'text-blue-600'
    },
    {
      title: 'Items Completed',
      value: '67',
      subtitle: 'Items Completed',
      change: '+8%',
      changeType: 'increase',
      period: 'vs avg',
      icon: <CheckCircle className="w-6 h-6 text-white" />,
      bgColor: 'from-green-50 to-green-100',
      borderColor: 'border-green-200',
      iconBg: 'bg-green-600',
      textColor: 'text-green-900',
      subtitleColor: 'text-green-700',
      changeColor: 'text-green-600',
      periodColor: 'text-green-600'
    },
    {
      title: 'Severe Deviations',
      value: '23',
      subtitle: 'Severe Deviations',
      change: '+3',
      changeType: 'increase',
      period: 'this week',
      icon: <AlertTriangle className="w-6 h-6 text-white" />,
      bgColor: 'from-amber-50 to-amber-100',
      borderColor: 'border-amber-200',
      iconBg: 'bg-amber-600',
      textColor: 'text-amber-900',
      subtitleColor: 'text-amber-700',
      changeColor: 'text-red-600',
      periodColor: 'text-amber-600'
    },
    {
      title: 'Archivists Online',
      value: '8',
      subtitle: 'Archivists Online',
      change: 'Peak: 12',
      changeType: 'neutral',
      period: 'today',
      icon: <Users className="w-6 h-6 text-white" />,
      bgColor: 'from-purple-50 to-purple-100',
      borderColor: 'border-purple-200',
      iconBg: 'bg-purple-600',
      textColor: 'text-purple-900',
      subtitleColor: 'text-purple-700',
      changeColor: 'text-green-600',
      periodColor: 'text-purple-600'
    }
  ]

  const performanceData = [
    {
      name: 'Archivist A',
      role: 'Senior Archivist',
      completed: 47,
      avgTime: '12.3 min',
      accuracy: 96,
      severeDeviations: 3,
      status: 'Online',
      statusColor: 'bg-green-100 text-green-800'
    },
    {
      name: 'Archivist B',
      role: 'Archivist',
      completed: 39,
      avgTime: '15.7 min',
      accuracy: 93,
      severeDeviations: 7,
      status: 'Online',
      statusColor: 'bg-green-100 text-green-800'
    },
    {
      name: 'Archivist C',
      role: 'Junior Archivist',
      completed: 31,
      avgTime: '18.2 min',
      accuracy: 89,
      severeDeviations: 12,
      status: 'Away',
      statusColor: 'bg-yellow-100 text-yellow-800'
    },
    {
      name: 'Archivist D',
      role: 'Senior Archivist',
      completed: 43,
      avgTime: '13.9 min',
      accuracy: 95,
      severeDeviations: 1,
      status: 'Offline',
      statusColor: 'bg-gray-100 text-gray-800'
    }
  ]

  return (
    <section id="metrics-dashboard" className="bg-white px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h3 className="text-xl font-semibold text-museum-900 mb-2">Backlog Metrics & Performance</h3>
          <p className="text-museum-600">Real-time monitoring of archival processing workflow and system performance</p>
        </div>

        {/* Key Performance Indicators */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          {kpiData.map((kpi, index) => (
            <div key={index} className={`bg-gradient-to-br ${kpi.bgColor} rounded-xl p-6 border ${kpi.borderColor}`}>
              <div className="flex items-center justify-between mb-4">
                <div className={`w-12 h-12 ${kpi.iconBg} rounded-lg flex items-center justify-center`}>
                  {kpi.icon}
                </div>
                <span className={`text-sm ${kpi.periodColor} font-medium`}>Daily</span>
              </div>
              <div className="mb-2">
                <h4 className={`text-2xl font-bold ${kpi.textColor}`}>{kpi.value}</h4>
                <p className={`text-sm ${kpi.subtitleColor}`}>{kpi.subtitle}</p>
              </div>
              <div className="flex items-center space-x-2">
                <span className={`text-xs ${kpi.changeColor} font-medium`}>
                  {kpi.changeType === 'increase' ? <TrendingUp className="w-3 h-3 inline mr-1" /> : null}
                  {kpi.change}
                </span>
                <span className={`text-xs ${kpi.periodColor}`}>{kpi.period}</span>
              </div>
            </div>
          ))}
        </div>

        {/* Performance Metrics Table */}
        <div className="bg-white rounded-xl shadow-sm border border-museum-200">
          <div className="p-6 border-b border-museum-200">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="text-lg font-semibold text-museum-900">Archivist Performance</h4>
                <p className="text-sm text-museum-500">Individual productivity metrics for the current week</p>
              </div>
              <div className="flex items-center space-x-2">
                <button className="px-3 py-1.5 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors text-sm">
                  Export Report
                </button>
                <select className="px-3 py-1.5 border border-museum-300 rounded text-sm focus:ring-2 focus:ring-museum-500 focus:border-transparent">
                  <option>This Week</option>
                  <option>Last Week</option>
                  <option>This Month</option>
                </select>
              </div>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-museum-50 border-b border-museum-200">
                <tr>
                  <th className="px-6 py-4 text-left text-sm font-semibold text-museum-900">Archivist</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Items Completed</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Avg. Time per Item</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Accuracy Rate</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Severe Deviations</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-museum-900">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-museum-200">
                {performanceData.map((archivist, index) => (
                  <tr key={index} className="hover:bg-museum-50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-center space-x-3">
                        <span
                          className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-museum-200 text-xs font-semibold text-museum-700"
                          aria-hidden="true"
                        >
                          {archivist.name.split(' ').map((part) => part[0]).join('')}
                        </span>
                        <div>
                          <p className="font-medium text-museum-900">{archivist.name}</p>
                          <p className="text-sm text-museum-500">{archivist.role}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className="text-lg font-semibold text-museum-900">{archivist.completed}</span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className="text-museum-900">{archivist.avgTime}</span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <div className="flex items-center justify-center space-x-2">
                        <div className="w-16 bg-museum-200 rounded-full h-2">
                          <div 
                            className={`h-2 rounded-full ${
                              archivist.accuracy >= 95 ? 'bg-green-500' : 
                              archivist.accuracy >= 90 ? 'bg-yellow-500' : 'bg-red-500'
                            }`}
                            style={{ width: `${archivist.accuracy}%` }}
                          ></div>
                        </div>
                        <span className={`text-sm font-medium ${
                          archivist.accuracy >= 95 ? 'text-green-600' : 
                          archivist.accuracy >= 90 ? 'text-yellow-600' : 'text-red-600'
                        }`}>
                          {archivist.accuracy}%
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className="text-museum-900">{archivist.severeDeviations}</span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${archivist.statusColor}`}>
                        <div className="w-2 h-2 bg-current rounded-full mr-1"></div>
                        {archivist.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  )
}

export default MetricsDashboard
