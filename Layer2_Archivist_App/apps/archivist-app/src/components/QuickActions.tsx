// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { Info, Download, Plus } from 'lucide-react'

interface QuickActionsProps {
  onInfoClick: () => void
  onDownloadClick: () => void
  onAddClick: () => void
}

const QuickActions: React.FC<QuickActionsProps> = ({
  onInfoClick,
  onDownloadClick,
  onAddClick
}) => {
  return (
    <div className="fixed bottom-6 right-6 z-40">
      <div className="flex flex-col items-end space-y-3">
        {/* <button
          className="w-12 h-12 bg-museum-800 text-white rounded-full shadow-lg hover:bg-museum-700 transition-colors flex items-center justify-center"
          onClick={onInfoClick}
          aria-label="Show queue information"
        >
          <Info className="w-6 h-6" />
        </button> */}
    
      </div>
    </div>
  )
}

export default QuickActions
