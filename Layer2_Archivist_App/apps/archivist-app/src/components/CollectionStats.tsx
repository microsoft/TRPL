'use client'

import React from 'react'
import { Files, Clock, CheckCircle, Building, TrendingUp, AlertTriangle, Check } from 'lucide-react'

interface CollectionStatsProps {
  totalItems: number
  totalPending: number
  totalCompleted: number
  numberOfCollections: number
}

const CollectionStats: React.FC<CollectionStatsProps> = ({
  totalItems,
  totalPending,
  totalCompleted,
  numberOfCollections
}) => {
  // Calculate completion rate
  const completionRate = totalItems > 0 
    ? ((totalItems-totalPending) / totalItems * 100).toFixed(1)
    : '0.0'

  const stats = [
    {
      title: 'Total Items',
      value: totalItems.toLocaleString(),
      subtitle: 'Digitized documents',
      change: `${numberOfCollections} collections`,
      changeType: 'increase',
      icon: <Files className="w-6 h-6 text-blue-600" />,
      iconBg: 'bg-blue-100',
      changeColor: 'text-museum-600',
      changeIcon: <TrendingUp className="w-3 h-3" />
    },
    {
      title: 'Pending Review',
      value: totalPending.toLocaleString(),
      subtitle: 'Awaiting validation',
      change: totalItems > 0 
        ? `${((totalPending / totalItems) * 100).toFixed(1)}% of total`
        : 'No items',
      changeType: 'warning',
      icon: <Clock className="w-6 h-6 text-amber-600" />,
      iconBg: 'bg-amber-100',
      changeColor: 'text-amber-600',
      changeIcon: <AlertTriangle className="w-3 h-3" />
    },
    {
      title: 'Ready for Publication',
      value: (totalItems - totalPending).toLocaleString(),
      subtitle: 'Validated documents',
      change: `${completionRate}% completion rate`,
      changeType: 'success',
      icon: <CheckCircle className="w-6 h-6 text-green-600" />,
      iconBg: 'bg-green-100',
      changeColor: 'text-green-600',
      changeIcon: <Check className="w-3 h-3" />
    },
    {
      title: 'Collections',
      value: numberOfCollections.toLocaleString(),
      subtitle: 'Repository collections',
      change: totalItems > 0 && numberOfCollections > 0
        ? `${(totalItems / numberOfCollections).toFixed(1)} avg per collection`
        : 'No collections',
      changeType: 'info',
      icon: <Building className="w-6 h-6 text-purple-600" />,
      iconBg: 'bg-purple-100',
      changeColor: 'text-museum-600',
      changeIcon: <Building className="w-3 h-3" />
    }
  ]

  return (
    <section id="collection-stats" className="px-6 py-8 bg-museum-50">
      <div className="max-w-7xl mx-auto">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {stats.map((stat, index) => (
            <div key={index} className="bg-gradient-to-br from-museum-50 to-museum-100 rounded-xl p-6 border border-museum-200">
              <div className="flex items-center justify-between mb-4">
                <div className={`w-12 h-12 ${stat.iconBg} rounded-lg flex items-center justify-center`}>
                  {stat.icon}
                </div>
                <span className="text-xs font-medium text-museum-600 bg-white px-2 py-1 rounded-full">
                  {stat.title}
                </span>
              </div>
              <div className="mb-2">
                <h3 className="text-2xl font-bold text-museum-900">{stat.value}</h3>
                <p className="text-sm text-museum-600">{stat.subtitle}</p>
              </div>
              <div className="flex items-center space-x-1 text-xs">
                <span className={stat.changeColor}>
                  {stat.changeIcon}
                </span>
                <span className={stat.changeColor}>{stat.change}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default CollectionStats
