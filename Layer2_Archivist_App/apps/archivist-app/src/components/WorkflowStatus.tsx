// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import { Upload, Bot, UserCheck, CheckCircle2, TrendingUp, RefreshCw, Clock, Check } from 'lucide-react'

const WorkflowStatus: React.FC = () => {
  const workflowSteps = [
    {
      title: 'Newly Ingested',
      value: '1,247',
      description: 'Awaiting AI processing',
      icon: <Upload className="w-6 h-6 text-blue-600" />,
      iconBg: 'bg-blue-100',
      change: '+47 today',
      changeColor: 'text-blue-600',
      changeIcon: <TrendingUp className="w-3 h-3" />
    },
    {
      title: 'AI Processing',
      value: '3,892',
      description: 'Transcription & metadata generation',
      icon: <Bot className="w-6 h-6 text-purple-600" />,
      iconBg: 'bg-purple-100',
      change: 'Processing...',
      changeColor: 'text-purple-600',
      changeIcon: <RefreshCw className="w-3 h-3 animate-spin" />
    },
    {
      title: 'Awaiting Review',
      value: '8,429',
      description: 'Ready for archivist validation',
      icon: <UserCheck className="w-6 h-6 text-amber-600" />,
      iconBg: 'bg-amber-100',
      change: 'Avg. 2.3 days in queue',
      changeColor: 'text-amber-600',
      changeIcon: <Clock className="w-3 h-3" />
    },
    {
      title: 'Publication Ready',
      value: '119,009',
      description: 'Validated and available',
      icon: <CheckCircle2 className="w-6 h-6 text-green-600" />,
      iconBg: 'bg-green-100',
      change: '+234 completed today',
      changeColor: 'text-green-600',
      changeIcon: <Check className="w-3 h-3" />
    }
  ]

  return (
    <section id="workflow-status" className="bg-museum-50 border-t border-museum-200 px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <h3 className="text-xl font-semibold text-museum-900 mb-6">Processing Workflow Status</h3>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {workflowSteps.map((step, index) => (
            <div key={index} className="bg-white rounded-lg border border-museum-200 p-6">
              <div className="flex items-center justify-between mb-4">
                <div className={`w-10 h-10 ${step.iconBg} rounded-lg flex items-center justify-center`}>
                  {step.icon}
                </div>
                <span className="text-2xl font-bold text-museum-900">{step.value}</span>
              </div>
              <h4 className="font-medium text-museum-900 mb-1">{step.title}</h4>
              <p className="text-sm text-museum-600">{step.description}</p>
              <div className="mt-3 text-xs flex items-center space-x-1">
                <span className={step.changeColor}>
                  {step.changeIcon}
                </span>
                <span className={step.changeColor}>{step.change}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default WorkflowStatus
