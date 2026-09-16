// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState } from 'react'
import { 
  Bot, 
  ZoomIn, 
  ZoomOut, 
  RotateCw, 
  Maximize, 
  Copy, 
  Eye, 
  Info
} from 'lucide-react'
import { ArchivalRecord } from '@/types'

interface SourcePanelProps {
  document: ArchivalRecord
  aiTranscription: string
  onTranscriptionChange: (transcription: string) => void
  onFormChange: () => void
}

const SourcePanel: React.FC<SourcePanelProps> = ({
  document,
  aiTranscription,
  onTranscriptionChange
}) => {
  const [currentPage, setCurrentPage] = useState(1)
  const [showAITranscription, setShowAITranscription] = useState(true)
  const [zoomLevel, setZoomLevel] = useState(100)

  const handleZoomIn = () => {
    setZoomLevel(prev => Math.min(prev + 25, 200))
  }

  const handleZoomOut = () => {
    setZoomLevel(prev => Math.max(prev - 25, 50))
  }

  const handleRotate = () => {
    // Rotate functionality would be implemented here
    console.log('Rotate document')
  }

  const handleFullscreen = () => {
    // Fullscreen functionality would be implemented here
    console.log('Toggle fullscreen')
  }

  const handleCopyTranscription = () => {
    navigator.clipboard.writeText(aiTranscription)
    // Show toast notification
  }


  return (
    <section className="w-1/2 bg-white border-r border-museum-200 flex flex-col">
      {/* Panel Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-museum-200 bg-museum-50">
        <div className="flex items-center space-x-3">
          <h3 className="text-lg font-semibold text-museum-900">Source Material</h3>
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
            <Bot className="w-3 h-3 mr-1" />
            AI Generated
          </span>
        </div>
        <div className="flex items-center space-x-2">
          <button 
            className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
            title="Zoom In"
            onClick={handleZoomIn}
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button 
            className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
            title="Zoom Out"
            onClick={handleZoomOut}
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <button 
            className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
            title="Rotate"
            onClick={handleRotate}
          >
            <RotateCw className="w-4 h-4" />
          </button>
          <button 
            className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors" 
            title="Fullscreen"
            onClick={handleFullscreen}
          >
            <Maximize className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Document Preview Area */}
      <div className="flex-1 overflow-hidden">
        <div className="h-full flex flex-col">
          {/* Preview Window */}
          <div className="flex-1 bg-museum-100 p-4 overflow-auto ocr-scrollbar">
            <div className="bg-white rounded-lg shadow-sm h-full flex items-center justify-center">
              <div
                className="flex aspect-[3/4] w-64 items-center justify-center rounded-lg border border-museum-300 bg-museum-50 text-center text-museum-600 shadow-lg"
                style={{ transform: `scale(${zoomLevel / 100})` }}
                role="img"
                aria-label="Neutral document preview placeholder"
              >
                Document preview
              </div>
            </div>
          </div>

          {/* Thumbnail Strip for Multi-page Documents */}
          <div className="border-t border-museum-200 p-3 bg-white">
            <div className="flex items-center space-x-2">
              <span className="text-sm font-medium text-museum-700">Pages:</span>
              <div className="flex space-x-2 overflow-x-auto ocr-scrollbar">
                <button 
                  className={`w-12 h-16 rounded flex items-center justify-center text-xs font-medium border-2 ${
                    currentPage === 1 
                      ? 'bg-museum-800 text-white border-museum-800' 
                      : 'bg-museum-100 border-museum-300 text-museum-600 hover:bg-museum-200'
                  }`}
                  onClick={() => setCurrentPage(1)}
                >
                  1
                </button>
                <button 
                  className={`w-12 h-16 rounded flex items-center justify-center text-xs font-medium border-2 ${
                    currentPage === 2 
                      ? 'bg-museum-800 text-white border-museum-800' 
                      : 'bg-museum-100 border-museum-300 text-museum-600 hover:bg-museum-200'
                  }`}
                  onClick={() => setCurrentPage(2)}
                >
                  2
                </button>
                <button 
                  className={`w-12 h-16 rounded flex items-center justify-center text-xs font-medium border-2 ${
                    currentPage === 3 
                      ? 'bg-museum-800 text-white border-museum-800' 
                      : 'bg-museum-100 border-museum-300 text-museum-600 hover:bg-museum-200'
                  }`}
                  onClick={() => setCurrentPage(3)}
                >
                  3
                </button>
              </div>
              <div className="ml-auto text-xs text-museum-500">
                Page {currentPage} of 3
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* AI Transcription Section */}
      <div className="border-t border-museum-200 bg-white">
        <div className="px-6 py-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-semibold text-museum-900">AI Transcription</h4>
            <div className="flex items-center space-x-2">
              <span className="text-xs text-museum-500">Confidence: {document.aiConfidence}%</span>
              <button 
                className="p-1 text-museum-500 hover:text-museum-700 transition-colors" 
                title="Copy Text"
                onClick={handleCopyTranscription}
              >
                <Copy className="w-3 h-3" />
              </button>
              <button 
                className="p-1 text-museum-500 hover:text-museum-700 transition-colors" 
                title="Toggle View"
                onClick={() => setShowAITranscription(!showAITranscription)}
              >
                <Eye className="w-3 h-3" />
              </button>
            </div>
          </div>
          {showAITranscription && (
            <div className="bg-museum-50 rounded-lg p-4 max-h-64 overflow-y-auto custom-scrollbar">
              <div className="text-sm text-museum-800 leading-relaxed">
                <p className="mb-3">My dear John,</p>
                <p className="mb-3">
                  I write to you in the utmost confidence regarding our ongoing negotiations with the{' '}
                  <span className="bg-red-100 text-red-800 px-1 rounded">Colombian</span> government concerning the Panama Canal. 
                  The situation has become increasingly{' '}
                  <span className="bg-red-100 text-red-800 px-1 rounded">delicate</span> and requires our most careful consideration.
                </p>
                <p className="mb-3">
                  The <span className="bg-red-100 text-red-800 px-1 rounded">French</span> interests, as you well know, have been pressing for a resolution that would be most advantageous to their position. 
                  However, I believe we must consider the broader implications for American commerce and strategic interests in the region.
                </p>
                <p className="mb-3">
                  I have been in correspondence with <span className="bg-red-100 text-red-800 px-1 rounded">Secretary Root</span> regarding this matter, 
                  and we are in agreement that a more decisive approach may be necessary. The current Colombian government&apos;s reluctance to accept our terms may require us to explore alternative arrangements.
                </p>
                <p>I trust in your discretion and await your counsel on this most pressing matter.</p>
                <p className="mt-3">
                  Most sincerely yours,<br />
                  Theodore Roosevelt
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* AI Metadata Section */}
      <div className="border-t border-museum-200 bg-white">
        <div className="px-6 py-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-semibold text-museum-900">AI-Generated Metadata</h4>
            <button 
              className="p-1 text-museum-500 hover:text-museum-700 transition-colors" 
              title="View Details"
            >
              <Info className="w-3 h-3" />
            </button>
          </div>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-museum-600 mb-1">Resource Type</label>
                <div className="px-3 py-2 bg-museum-50 rounded text-sm text-museum-800">
                  {document.resourceType.charAt(0).toUpperCase() + document.resourceType.slice(1)}
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-museum-600 mb-1">Production Method</label>
                <div className="px-3 py-2 bg-museum-50 rounded text-sm text-museum-800">Handwritten</div>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-museum-600 mb-1">Document Topics</label>
              <div className="flex flex-wrap gap-1">
                <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-800">Panama Canal</span>
                <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-800">Diplomacy</span>
                <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-800">Colombia Relations</span>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-museum-600 mb-1">Document Sentiment</label>
              <div className="px-3 py-2 bg-museum-50 rounded text-sm text-museum-800">Neutral</div>
            </div>
            <div>
              <label className="block text-xs font-medium text-museum-600 mb-1">Key Entities</label>
              <div className="text-sm text-museum-700">
                <div className="mb-1"><strong>People:</strong> John Hay, Elihu Root</div>
                <div className="mb-1"><strong>Places:</strong> Panama, Colombia, France</div>
                <div><strong>Organizations:</strong> Colombian Government</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default SourcePanel
