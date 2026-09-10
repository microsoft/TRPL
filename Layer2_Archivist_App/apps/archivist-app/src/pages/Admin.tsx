'use client'

import React, { useState } from 'react'
import Header from '@/components/Header'
import BreadcrumbNav from '@/components/BreadcrumbNav'
import AdminHeader from '@/components/AdminHeader'
import AdminTabs from '@/components/AdminTabs'
import SystemStatusAlert from '@/components/SystemStatusAlert'
import VocabularyManagement from '@/components/VocabularyManagement'
import MetricsDashboard from '@/components/MetricsDashboard'
import SystemConfiguration from '@/components/SystemConfiguration'
import DataIntegrationStatus from '@/components/DataIntegrationStatus'
import ActivityLog from '@/components/ActivityLog'
import QuickActionsPanel from '@/components/QuickActionsPanel'
import Footer from '@/components/Footer'
import ConfirmationModal from '@/components/ConfirmationModal'
import Toast from '@/components/Toast'

const AdminPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState('vocabularies')
  const [showConfirmation, setShowConfirmation] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)

  const handleTabChange = (tab: string) => {
    setActiveTab(tab)
  }

  const handleShowConfirmation = () => {
    setShowConfirmation(true)
  }

  const handleHideConfirmation = () => {
    setShowConfirmation(false)
  }

  const handleToastClose = () => {
    setToast(null)
  }

  const handleSaveAll = () => {
    setToast({ type: 'success', message: 'All settings have been saved successfully.' })
  }

  const handleExportSettings = () => {
    setToast({ type: 'info', message: 'Settings export initiated. Download will begin shortly.' })
  }

  return (
    <div className="min-h-screen bg-museum-50">
      
      <AdminHeader 
        onSaveAll={handleSaveAll}
        onExportSettings={handleExportSettings}
      />

      <AdminTabs 
        activeTab={activeTab} 
        onTabChange={handleTabChange} 
      />

      <SystemStatusAlert />

      {activeTab === 'vocabularies' && (
        <VocabularyManagement onShowConfirmation={handleShowConfirmation} />
      )}

      {activeTab === 'metrics' && (
        <MetricsDashboard />
      )}

      {activeTab === 'users' && (
        <div className="bg-white px-6 py-8">
          <div className="max-w-7xl mx-auto">
            <h3 className="text-xl font-semibold text-museum-900 mb-2">User Management</h3>
            <p className="text-museum-600">Manage user accounts and permissions</p>
            <div className="mt-8 text-center text-museum-500">
              <p>User management interface coming soon...</p>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'settings' && (
        <SystemConfiguration />
      )}

      {activeTab === 'audit' && (
        <ActivityLog />
      )}

      <DataIntegrationStatus />

      <QuickActionsPanel />

      <Footer />

      {showConfirmation && (
        <ConfirmationModal 
          onClose={handleHideConfirmation}
          onConfirm={() => {
            handleHideConfirmation()
            setToast({ type: 'success', message: 'Action completed successfully.' })
          }}
        />
      )}

      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={handleToastClose}
          />
        </div>
      )}
    </div>
  )
}

export default AdminPage
