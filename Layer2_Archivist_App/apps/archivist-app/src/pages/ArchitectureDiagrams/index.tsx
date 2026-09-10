'use client'

import { useState } from 'react'
import { Activity, Layers } from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import DataPipelineDiagram from './DataPipelineDiagram'
import AppArchitectureDiagram from './AppArchitectureDiagram'

type DiagramType = 'pipeline' | 'app'

export default function ArchitectureDiagrams() {
  const [activeDiagram, setActiveDiagram] = useState<DiagramType>('app')

  const diagrams = [
    {
      id: 'app' as DiagramType,
      title: 'Application Architecture',
      icon: Layers,
      bgColor: 'bg-indigo-600',
      hoverColor: 'hover:bg-indigo-700',
    },
    {
      id: 'pipeline' as DiagramType,
      title: 'Data Pipeline Architecture',
      icon: Activity,
      bgColor: 'bg-orange-500',
      hoverColor: 'hover:bg-orange-600',
    },
  ]

  return (
    <div className="bg-gray-50 min-h-full">
      <PageHeader
        title="Architecture Diagrams"
        subtitle="Technical architecture documentation for the Archivist platform"
      />

      {/* Diagram Selector */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center gap-3">
          {diagrams.map((diagram) => {
            const Icon = diagram.icon
            const isActive = activeDiagram === diagram.id
            return (
              <button
                key={diagram.id}
                onClick={() => setActiveDiagram(diagram.id)}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium transition-all ${
                  isActive
                    ? `${diagram.bgColor} text-white shadow-md`
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span>{diagram.title}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Diagram Content */}
      <div className="p-6">
        {activeDiagram === 'pipeline' ? <DataPipelineDiagram /> : <AppArchitectureDiagram />}
      </div>
    </div>
  )
}
