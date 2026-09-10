import { Database, Clock, CheckCircle2, AlertTriangle, ScanText } from 'lucide-react'
import StatCard from './StatCard'
import type { PipelineStats } from '../types'

interface StatisticsOverviewProps {
  stats: PipelineStats | null
}

export default function StatisticsOverview({ stats }: StatisticsOverviewProps) {
  return (
    <>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-900">Statistics Overview</h2>
        {stats?.last_updated && (
          <span className="text-sm text-gray-500">
            Last updated: {new Date(stats.last_updated).toLocaleString()}
          </span>
        )}
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6">
        <StatCard
          icon={<Database className="w-5 h-5 text-indigo-600" />}
          iconBg="bg-indigo-100"
          label="Total Records"
          value={stats?.total_records ?? '—'}
        />
        
        <StatCard
          icon={<Clock className="w-5 h-5 text-amber-600" />}
          iconBg="bg-amber-100"
          label="Pending"
          value={stats?.overall?.pending ?? 0}
          valueColor="text-amber-600"
        />
        
        <StatCard
          icon={<CheckCircle2 className="w-5 h-5 text-green-600" />}
          iconBg="bg-green-100"
          label="Completed"
          value={stats?.overall?.completed ?? 0}
          valueColor="text-green-600"
        />
        
        <StatCard
          icon={<AlertTriangle className="w-5 h-5 text-red-600" />}
          iconBg="bg-red-100"
          label="Errors"
          value={stats?.overall?.error ?? 0}
          valueColor="text-red-600"
        />
        
        <StatCard
          icon={<ScanText className="w-5 h-5 text-blue-600" />}
          iconBg="bg-blue-100"
          label="Active Batches"
          value={stats?.active_batches ?? 0}
          valueColor="text-blue-600"
        />
      </div>
    </>
  )
}

