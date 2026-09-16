// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { Clock, CheckCircle2, XCircle, Loader2, Pause, Square } from 'lucide-react'

interface StatusBadgeProps {
  status: string
}

const badges: Record<string, { bg: string; text: string; icon: React.ComponentType<{ className?: string }> }> = {
  pending: { bg: 'bg-amber-100', text: 'text-amber-800', icon: Clock },
  completed: { bg: 'bg-green-100', text: 'text-green-800', icon: CheckCircle2 },
  error: { bg: 'bg-red-100', text: 'text-red-800', icon: XCircle },
  Running: { bg: 'bg-blue-100', text: 'text-blue-800', icon: Loader2 },
  Pending: { bg: 'bg-gray-100', text: 'text-gray-800', icon: Clock },
  Completed: { bg: 'bg-green-100', text: 'text-green-800', icon: CheckCircle2 },
  Failed: { bg: 'bg-red-100', text: 'text-red-800', icon: XCircle },
  Suspended: { bg: 'bg-purple-100', text: 'text-purple-800', icon: Pause },
  Terminated: { bg: 'bg-gray-100', text: 'text-gray-800', icon: Square },
  Canceled: { bg: 'bg-gray-100', text: 'text-gray-800', icon: Square }
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const badge = badges[status] || badges.pending
  const Icon = badge.icon

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${badge.bg} ${badge.text}`}>
      <Icon className={`w-3 h-3 ${status === 'Running' ? 'animate-spin' : ''}`} />
      {status}
    </span>
  )
}

