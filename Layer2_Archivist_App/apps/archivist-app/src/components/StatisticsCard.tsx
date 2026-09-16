// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React from 'react'
import { LucideIcon } from 'lucide-react'

interface StatisticsCardProps {
  title: string
  value: string | number
  icon: LucideIcon
  iconBgColor?: string
  iconColor?: string
  subtitle?: string
  badge?: {
    text: string
    className?: string
  }
  variant?: 'default' | 'compact'
  className?: string
}

const StatisticsCard: React.FC<StatisticsCardProps> = ({
  title,
  value,
  icon: Icon,
  iconBgColor = 'bg-blue-100',
  iconColor = 'text-blue-600',
  subtitle,
  badge,
  variant = 'default',
  className = ''
}) => {
  const formattedValue = typeof value === 'number' ? value.toLocaleString() : value

  if (variant === 'compact') {
    return (
      <div className={`bg-white rounded-lg border border-gray-200 p-3 shadow-sm ${className}`}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-medium text-gray-600">{title}</p>
            <p className="text-xl font-bold text-gray-900">{formattedValue}</p>
          </div>
          <div className={`w-8 h-8 ${iconBgColor} rounded-lg flex items-center justify-center`}>
            <Icon className={`w-4 h-4 ${iconColor}`} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`bg-white rounded-lg border border-gray-200 p-4 shadow-sm hover:shadow-md transition-shadow ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <div className={`w-12 h-12 ${iconBgColor} rounded-lg flex items-center justify-center`}>
          <Icon className={`w-6 h-6 ${iconColor}`} />
        </div>
        {badge && (
          <div className={`px-2.5 py-1 rounded-full text-xs font-medium border ${badge.className || ''}`}>
            {badge.text}
          </div>
        )}
      </div>
      <div>
        <p className="text-xs font-medium text-gray-600 mb-1">{title}</p>
        <p className="text-3xl font-bold text-gray-900">{formattedValue}</p>
        {subtitle && (
          <p className="text-xs text-gray-500 mt-1">{subtitle}</p>
        )}
      </div>
    </div>
  )
}

export default StatisticsCard

