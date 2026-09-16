// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import { Shield, Download, Save } from 'lucide-react'

interface AdminHeaderProps {
  onSaveAll: () => void
  onExportSettings: () => void
}

const AdminHeader: React.FC<AdminHeaderProps> = ({ onSaveAll, onExportSettings }) => {
  return (
    <section id="admin-header" className="bg-white border-b border-museum-200 px-6 py-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between space-y-4 lg:space-y-0">
        <div>
          <div className="flex items-center space-x-3 mb-2">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <Shield className="text-red-600 text-lg" />
            </div>
            <div>
              <h2 className="text-2xl font-semibold text-museum-900">Admin Panel</h2>
              <p className="text-museum-600">Vocabulary management and system monitoring</p>
            </div>
          </div>
          <div className="flex items-center space-x-2 text-sm">
            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
              <Shield className="w-3 h-3 mr-1" />
              Admin Access Required
            </span>
            <span className="text-museum-500">•</span>
            <span className="text-museum-500">Role: Administrator</span>
          </div>
        </div>
        <div className="flex items-center space-x-4">
          <button 
            className="px-4 py-2 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors flex items-center space-x-2"
            onClick={onExportSettings}
          >
            <Download className="w-4 h-4" />
            <span>Export Settings</span>
          </button>
          <button 
            className="px-4 py-2 bg-museum-800 text-white rounded-lg hover:bg-museum-700 transition-colors flex items-center space-x-2"
            onClick={onSaveAll}
          >
            <Save className="w-4 h-4" />
            <span>Save All Changes</span>
          </button>
        </div>
      </div>
    </section>
  )
}

export default AdminHeader
