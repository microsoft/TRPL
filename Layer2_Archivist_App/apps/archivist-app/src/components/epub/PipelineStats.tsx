import React from 'react'
import { 
  Clock, 
  CheckCircle2, 
  Eye, 
  Loader2,
  ChevronRight,
  Book,
  RefreshCw
} from 'lucide-react'
import type { EpubDocument } from './types'

interface PipelineStatsProps {
  documents: EpubDocument[]
  onRefresh: () => void
}

const PipelineStats: React.FC<PipelineStatsProps> = ({ documents, onRefresh }) => {
  // Calculate stats by status
  const statusCounts = documents.reduce((acc, doc) => {
    acc[doc.status] = (acc[doc.status] || 0) + 1
    return acc
  }, {} as Record<string, number>)

  const pipelineStages = [
    {
      label: 'Uploaded',
      count: statusCounts.uploaded || 0,
      icon: Clock,
      bgColor: 'bg-blue-500',
      lightBg: 'bg-blue-50',
      textColor: 'text-blue-700',
      borderColor: 'border-blue-200'
    },
    {
      label: 'Parsed',
      count: (statusCounts.parsing || 0) + (statusCounts.parsed || 0),
      icon: CheckCircle2,
      bgColor: 'bg-green-500',
      lightBg: 'bg-green-50',
      textColor: 'text-green-700',
      borderColor: 'border-green-200'
    },
    {
      label: 'Validate',
      count: (statusCounts.extracting || 0) + (statusCounts.validate || 0),
      icon: Eye,
      bgColor: 'bg-orange-500',
      lightBg: 'bg-orange-50',
      textColor: 'text-orange-700',
      borderColor: 'border-orange-200'
    },
    {
      label: 'Processing',
      count: statusCounts.processing || 0,
      icon: Loader2,
      bgColor: 'bg-purple-500',
      lightBg: 'bg-purple-50',
      textColor: 'text-purple-700',
      borderColor: 'border-purple-200',
      spin: true
    },
    {
      label: 'Completed',
      count: statusCounts.completed || 0,
      icon: CheckCircle2,
      bgColor: 'bg-emerald-500',
      lightBg: 'bg-emerald-50',
      textColor: 'text-emerald-700',
      borderColor: 'border-emerald-200'
    }
  ]

  return (
    <div className="px-6 py-3 bg-white border-b border-gray-200">
      <div className="flex items-center gap-4">
        {/* Summary Stats */}
        <div className="flex items-center gap-5 pr-5 border-r border-gray-200">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-indigo-100 rounded-lg flex items-center justify-center">
              <Book className="w-4 h-4 text-indigo-600" />
            </div>
            <div>
              <p className="text-xs text-gray-500">Total Documents</p>
              <p className="text-base font-bold text-gray-900">{documents.length}</p>
            </div>
          </div>
          <button
            onClick={onRefresh}
            className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors flex items-center gap-1.5"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>

        {/* Pipeline Stats */}
        <div className="flex items-center gap-3 flex-1">
          {pipelineStages.map((stage, index) => {
            const Icon = stage.icon
            
            return (
              <React.Fragment key={stage.label}>
                <div className={`flex-1 flex items-center gap-2 px-3 py-2 ${stage.lightBg} rounded-lg border ${stage.borderColor}`}>
                  <div className={`w-7 h-7 ${stage.bgColor} rounded flex items-center justify-center flex-shrink-0`}>
                    <Icon className={`w-3.5 h-3.5 text-white ${stage.spin && stage.count > 0 ? 'animate-spin' : ''}`} />
                  </div>
                  <div className="text-left">
                    <p className="text-base font-bold text-gray-900 leading-tight">{stage.count}</p>
                    <p className={`text-xs ${stage.textColor}`}>{stage.label}</p>
                  </div>
                </div>
                {index < pipelineStages.length - 1 && (
                  <ChevronRight className="w-4 h-4 text-gray-300 flex-shrink-0" />
                )}
              </React.Fragment>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default PipelineStats

