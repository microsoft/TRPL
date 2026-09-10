'use client'

import React from 'react'

interface AdminTabsProps {
  activeTab: string
  onTabChange: (tab: string) => void
}

const AdminTabs: React.FC<AdminTabsProps> = ({ activeTab, onTabChange }) => {
  const tabs = [
    { id: 'vocabularies', label: 'Vocabularies' },
    { id: 'metrics', label: 'Metrics Dashboard' },
    { id: 'users', label: 'User Management' },
    { id: 'settings', label: 'System Settings' },
    { id: 'audit', label: 'Audit Logs' },
  ]

  return (
    <section id="admin-tabs" className="bg-white border-b border-museum-200 px-6">
      <nav className="flex space-x-8">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`py-4 px-2 border-b-2 font-medium text-sm transition-colors ${
              activeTab === tab.id
                ? 'border-museum-800 text-museum-900'
                : 'border-transparent text-museum-500 hover:text-museum-700'
            }`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
    </section>
  )
}

export default AdminTabs
