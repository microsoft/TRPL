import React from 'react'
import { 
  Clock, 
  Loader2, 
  CheckCircle2, 
  Eye, 
  XCircle,
  Trash2
} from 'lucide-react'
import type { StatusConfig } from './types'

export const statusConfig: Record<string, StatusConfig> = {
  uploaded: { color: 'text-blue-700', bgColor: 'bg-blue-100', icon: <Clock className="w-3.5 h-3.5" />, label: 'Uploaded' },
  parsing: { color: 'text-amber-700', bgColor: 'bg-amber-100', icon: <Loader2 className="w-3.5 h-3.5 animate-spin" />, label: 'Parsing' },
  parsed: { color: 'text-green-700', bgColor: 'bg-green-100', icon: <CheckCircle2 className="w-3.5 h-3.5" />, label: 'Parsed' },
  extracting: { color: 'text-cyan-700', bgColor: 'bg-cyan-100', icon: <Loader2 className="w-3.5 h-3.5 animate-spin" />, label: 'Extracting' },
  validate: { color: 'text-orange-700', bgColor: 'bg-orange-100', icon: <Eye className="w-3.5 h-3.5" />, label: 'Validate' },
  processing: { color: 'text-purple-700', bgColor: 'bg-purple-100', icon: <Loader2 className="w-3.5 h-3.5 animate-spin" />, label: 'Processing' },
  completed: { color: 'text-emerald-700', bgColor: 'bg-emerald-100', icon: <CheckCircle2 className="w-3.5 h-3.5" />, label: 'Completed' },
  deleting: { color: 'text-gray-700', bgColor: 'bg-gray-100', icon: <Loader2 className="w-3.5 h-3.5 animate-spin" />, label: 'Deleting' },
  error: { color: 'text-red-700', bgColor: 'bg-red-100', icon: <XCircle className="w-3.5 h-3.5" />, label: 'Error' },
}

interface EpubStatusBadgeProps {
  status: string
  showLabel?: boolean
  size?: 'sm' | 'md'
}

const EpubStatusBadge: React.FC<EpubStatusBadgeProps> = ({ 
  status, 
  showLabel = true,
  size = 'sm' 
}) => {
  const config = statusConfig[status] || statusConfig.error
  const sizeClasses = size === 'sm' ? 'px-1.5 py-0.5 text-xs' : 'px-2 py-1 text-xs'
  
  return (
    <span className={`inline-flex items-center gap-0.5 ${sizeClasses} rounded ${config.bgColor} ${config.color}`}>
      {config.icon}
      {showLabel && config.label}
    </span>
  )
}

export default EpubStatusBadge

