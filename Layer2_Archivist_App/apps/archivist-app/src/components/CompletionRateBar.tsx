// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React from 'react'

interface CompletionRateBarProps {
  rate: number
  showLabel?: boolean
  className?: string
}

const CompletionRateBar: React.FC<CompletionRateBarProps> = ({
  rate,
  showLabel = true,
  className = ''
}) => {
  const getBarColor = (rate: number): string => {
    if (rate >= 80) return 'bg-green-500'
    if (rate >= 60) return 'bg-blue-500'
    if (rate >= 40) return 'bg-yellow-500'
    return 'bg-red-500'
  }

  const getTextColor = (rate: number): string => {
    if (rate >= 80) return 'text-green-600'
    if (rate >= 60) return 'text-blue-600'
    if (rate >= 40) return 'text-yellow-600'
    return 'text-red-600'
  }

  return (
    <div className={`flex items-center space-x-2 ${className}`}>
      <div className="flex-1 bg-gray-200 rounded-full h-2">
        <div
          className={`h-2 rounded-full ${getBarColor(rate)}`}
          style={{ width: `${Math.min(100, Math.max(0, rate))}%` }}
        ></div>
      </div>
      {showLabel && (
        <span className={`text-sm font-medium ${getTextColor(rate)}`}>
          {rate.toFixed(1)}%
        </span>
      )}
    </div>
  )
}

export default CompletionRateBar

