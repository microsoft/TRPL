// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

interface StatCardProps {
  icon: React.ReactNode
  iconBg: string
  label: string
  value: string | number
  valueColor?: string
}

export default function StatCard({ icon, iconBg, label, value, valueColor = 'text-gray-900' }: StatCardProps) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
      <div className="flex items-center gap-3">
        <div className={`p-2 rounded-lg ${iconBg}`}>
          {icon}
        </div>
        <div>
          <p className="text-sm text-gray-500">{label}</p>
          <p className={`text-2xl font-bold ${valueColor}`}>
            {typeof value === 'number' ? value.toLocaleString() : value}
          </p>
        </div>
      </div>
    </div>
  )
}

