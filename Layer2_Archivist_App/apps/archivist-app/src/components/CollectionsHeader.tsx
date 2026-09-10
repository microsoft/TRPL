'use client'

import React from 'react'
import { RefreshCw } from 'lucide-react'

interface CollectionsHeaderProps {
  onSyncCollections: () => void
}

const CollectionsHeader: React.FC<CollectionsHeaderProps> = ({ onSyncCollections }) => {
  return (
    <section id="page-header" className="bg-white border-b border-museum-200 px-6 py-8">
      <div className="max-w-7xl mx-auto flex flex-col items-center text-center">
        {/* <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between space-y-4 lg:space-y-0"> */}
          <div>
            <h2 className="text-3xl font-anton font-semibold text-museum-900 mb-2">Digitized Roosevelt Collections</h2>
            <p className="text-museum-600 font-source-serif max-w-2xl">
              Comprehensive archive of Theodore Roosevelt materials from partner institutions. 
              All items undergo AI-assisted transcription and metadata generation, requiring archivist validation before public access.
            </p>
          </div>
          {/* <div className="flex items-center space-x-3">
            <button 
              className="px-4 py-2 bg-museum-accent text-white  hover:bg-museum-700 transition-colors flex items-center space-x-1.5 text-sm"
              onClick={onSyncCollections}
            >
              <RefreshCw className="w-4 h-4" />
              <span>Sync Collections</span>
            </button>
          </div> */}
        {/* </div> */}
      </div>
    </section>
  )
}

export default CollectionsHeader
