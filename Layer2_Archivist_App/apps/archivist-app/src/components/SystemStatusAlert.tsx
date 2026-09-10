'use client'

import React from 'react'
import { CheckCircle, ExternalLink } from 'lucide-react'

const SystemStatusAlert: React.FC = () => {
  return (
    <section id="system-status" className="bg-green-50 border-b border-green-200 px-6 py-4">
      <div className="flex items-center space-x-3">
        <div className="w-5 h-5 bg-green-500 rounded-full flex items-center justify-center">
          <CheckCircle className="text-white text-xs" />
        </div>
        <div className="flex-1">
          <p className="text-sm font-medium text-green-900">All systems operational</p>
          <p className="text-sm text-green-700">Last system check completed 5 minutes ago • Next scheduled maintenance: March 15, 2025</p>
        </div>
        <button className="text-green-600 hover:text-green-800">
          <ExternalLink className="w-4 h-4" />
        </button>
      </div>
    </section>
  )
}

export default SystemStatusAlert
