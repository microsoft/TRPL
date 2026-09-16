// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState } from 'react'
import { Brain, Workflow } from 'lucide-react'

const SystemConfiguration: React.FC = () => {
  const [aiThresholds, setAiThresholds] = useState({
    severeDeviation: 30,
    autoComplete: 95,
    priorityQueue: 50
  })

  const [workflowSettings, setWorkflowSettings] = useState({
    defaultSort: 'oldest',
    itemsPerPage: 50,
    sessionTimeout: 60
  })

  const [features, setFeatures] = useState({
    emailNotifications: true,
    autoSaveDrafts: true,
    requireReviewNotes: true,
    enableAutoComplete: false
  })

  const handleThresholdChange = (key: string, value: number) => {
    setAiThresholds(prev => ({ ...prev, [key]: value }))
  }

  const handleWorkflowChange = (key: string, value: string | number) => {
    setWorkflowSettings(prev => ({ ...prev, [key]: value }))
  }

  const handleFeatureToggle = (key: string) => {
    setFeatures(prev => ({ ...prev, [key]: !prev[key as keyof typeof prev] }))
  }

  return (
    <section id="system-config" className="bg-museum-50 px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h3 className="text-xl font-semibold text-museum-900 mb-2">System Configuration</h3>
          <p className="text-museum-600">Advanced settings for workflow automation and data processing</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* AI Confidence Thresholds */}
          <div className="bg-white rounded-xl shadow-sm border border-museum-200 p-6">
            <div className="flex items-center space-x-3 mb-6">
              <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                <Brain className="w-5 h-5 text-blue-600" />
              </div>
              <div>
                <h4 className="text-lg font-semibold text-museum-900">AI Processing Thresholds</h4>
                <p className="text-sm text-museum-500">Configure confidence levels for automatic flagging</p>
              </div>
            </div>

            <div className="space-y-6">
              <div>
                <label className="block text-sm font-medium text-museum-700 mb-3">Severe Deviation Threshold</label>
                <div className="flex items-center space-x-4">
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={aiThresholds.severeDeviation}
                    onChange={(e) => handleThresholdChange('severeDeviation', parseInt(e.target.value))}
                    className="flex-1 h-2 bg-museum-200 rounded-lg appearance-none cursor-pointer"
                  />
                  <div className="w-16 px-3 py-1 border border-museum-300 rounded text-sm text-center">
                    {aiThresholds.severeDeviation}%
                  </div>
                </div>
                <p className="text-xs text-museum-500 mt-1">Items with confidence below this threshold will be flagged for severe deviation</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-museum-700 mb-3">Auto-Complete Threshold</label>
                <div className="flex items-center space-x-4">
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={aiThresholds.autoComplete}
                    onChange={(e) => handleThresholdChange('autoComplete', parseInt(e.target.value))}
                    className="flex-1 h-2 bg-museum-200 rounded-lg appearance-none cursor-pointer"
                  />
                  <div className="w-16 px-3 py-1 border border-museum-300 rounded text-sm text-center">
                    {aiThresholds.autoComplete}%
                  </div>
                </div>
                <p className="text-xs text-museum-500 mt-1">Items with confidence above this threshold can be auto-completed (if enabled)</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-museum-700 mb-3">Priority Queue Threshold</label>
                <div className="flex items-center space-x-4">
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={aiThresholds.priorityQueue}
                    onChange={(e) => handleThresholdChange('priorityQueue', parseInt(e.target.value))}
                    className="flex-1 h-2 bg-museum-200 rounded-lg appearance-none cursor-pointer"
                  />
                  <div className="w-16 px-3 py-1 border border-museum-300 rounded text-sm text-center">
                    {aiThresholds.priorityQueue}%
                  </div>
                </div>
                <p className="text-xs text-museum-500 mt-1">Items with confidence below this threshold will be prioritized in the queue</p>
              </div>

              <div className="border-t border-museum-200 pt-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h5 className="font-medium text-museum-900">Enable Auto-Complete</h5>
                    <p className="text-sm text-museum-500">Automatically mark high-confidence items as complete</p>
                  </div>
                  <button
                    onClick={() => handleFeatureToggle('enableAutoComplete')}
                    className={`relative inline-block w-12 h-6 rounded-full transition-colors ${
                      features.enableAutoComplete ? 'bg-blue-500' : 'bg-museum-300'
                    }`}
                  >
                    <div className={`absolute top-1 bg-white rounded-full w-4 h-4 transition-transform ${
                      features.enableAutoComplete ? 'right-1' : 'left-1'
                    }`}></div>
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Workflow Settings */}
          <div className="bg-white rounded-xl shadow-sm border border-museum-200 p-6">
            <div className="flex items-center space-x-3 mb-6">
              <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
                <Workflow className="w-5 h-5 text-green-600" />
              </div>
              <div>
                <h4 className="text-lg font-semibold text-museum-900">Workflow Settings</h4>
                <p className="text-sm text-museum-500">Configure processing rules and notifications</p>
              </div>
            </div>

            <div className="space-y-6">
              <div>
                <label className="block text-sm font-medium text-museum-700 mb-2">Default Queue Sort Order</label>
                <select
                  value={workflowSettings.defaultSort}
                  onChange={(e) => handleWorkflowChange('defaultSort', e.target.value)}
                  className="w-full px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent"
                >
                  <option value="oldest">Oldest First</option>
                  <option value="confidence-low">Lowest Confidence First</option>
                  <option value="source">By Source System</option>
                  <option value="type">By Resource Type</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-museum-700 mb-2">Items Per Page (Default)</label>
                <select
                  value={workflowSettings.itemsPerPage}
                  onChange={(e) => handleWorkflowChange('itemsPerPage', parseInt(e.target.value))}
                  className="w-full px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent"
                >
                  <option value={25}>25</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={200}>200</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-museum-700 mb-2">Session Timeout (minutes)</label>
                <input
                  type="number"
                  value={workflowSettings.sessionTimeout}
                  onChange={(e) => handleWorkflowChange('sessionTimeout', parseInt(e.target.value))}
                  min="15"
                  max="480"
                  className="w-full px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent"
                />
              </div>

              <div className="space-y-4 border-t border-museum-200 pt-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h5 className="font-medium text-museum-900">Email Notifications</h5>
                    <p className="text-sm text-museum-500">Send alerts for severe deviations</p>
                  </div>
                  <button
                    onClick={() => handleFeatureToggle('emailNotifications')}
                    className={`relative inline-block w-12 h-6 rounded-full transition-colors ${
                      features.emailNotifications ? 'bg-blue-500' : 'bg-museum-300'
                    }`}
                  >
                    <div className={`absolute top-1 bg-white rounded-full w-4 h-4 transition-transform ${
                      features.emailNotifications ? 'right-1' : 'left-1'
                    }`}></div>
                  </button>
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <h5 className="font-medium text-museum-900">Auto-Save Drafts</h5>
                    <p className="text-sm text-museum-500">Save work in progress every 30 seconds</p>
                  </div>
                  <button
                    onClick={() => handleFeatureToggle('autoSaveDrafts')}
                    className={`relative inline-block w-12 h-6 rounded-full transition-colors ${
                      features.autoSaveDrafts ? 'bg-blue-500' : 'bg-museum-300'
                    }`}
                  >
                    <div className={`absolute top-1 bg-white rounded-full w-4 h-4 transition-transform ${
                      features.autoSaveDrafts ? 'right-1' : 'left-1'
                    }`}></div>
                  </button>
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <h5 className="font-medium text-museum-900">Require Review Notes</h5>
                    <p className="text-sm text-museum-500">Mandatory notes for severe deviations</p>
                  </div>
                  <button
                    onClick={() => handleFeatureToggle('requireReviewNotes')}
                    className={`relative inline-block w-12 h-6 rounded-full transition-colors ${
                      features.requireReviewNotes ? 'bg-blue-500' : 'bg-museum-300'
                    }`}
                  >
                    <div className={`absolute top-1 bg-white rounded-full w-4 h-4 transition-transform ${
                      features.requireReviewNotes ? 'right-1' : 'left-1'
                    }`}></div>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default SystemConfiguration
