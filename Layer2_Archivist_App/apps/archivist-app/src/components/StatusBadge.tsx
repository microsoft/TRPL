import React from 'react'
import { CheckCircle2, TrendingUp, AlertCircle, TrendingDown, BarChart3, LucideIcon } from 'lucide-react'

export type StatusType = 'excellent' | 'good' | 'warning' | 'critical'

interface StatusBadgeProps {
  status: StatusType
  showIcon?: boolean
  className?: string
}

const getStatusColor = (status: StatusType): string => {
  switch (status) {
    case 'excellent':
      return 'bg-green-100 text-green-700 border-green-200'
    case 'good':
      return 'bg-blue-100 text-blue-700 border-blue-200'
    case 'warning':
      return 'bg-yellow-100 text-yellow-700 border-yellow-200'
    case 'critical':
      return 'bg-red-100 text-red-700 border-red-200'
    default:
      return 'bg-gray-100 text-gray-700 border-gray-200'
  }
}

const getStatusIcon = (status: StatusType): LucideIcon => {
  switch (status) {
    case 'excellent':
      return CheckCircle2
    case 'good':
      return TrendingUp
    case 'warning':
      return AlertCircle
    case 'critical':
      return TrendingDown
    default:
      return BarChart3
  }
}

const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  showIcon = true,
  className = ''
}) => {
  const Icon = getStatusIcon(status)
  const statusText = status.charAt(0).toUpperCase() + status.slice(1)

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${getStatusColor(status)} ${className}`}>
      {showIcon && <Icon className="w-4 h-4" />}
      <span>{statusText}</span>
    </span>
  )
}

export default StatusBadge
export { getStatusColor, getStatusIcon }

