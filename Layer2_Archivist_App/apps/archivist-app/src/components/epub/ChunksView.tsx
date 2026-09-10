import React, { useState, useMemo } from 'react'
import { 
  Loader2, 
  FileText,
  Hash,
  Search,
  ChevronDown,
  ChevronUp
} from 'lucide-react'
import type { Chunk } from './types'

interface ChunksViewProps {
  chunks: Chunk[]
  isLoading: boolean
}

const ChunksView: React.FC<ChunksViewProps> = ({
  chunks,
  isLoading
}) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedChunkIndex, setSelectedChunkIndex] = useState<number | null>(null)
  const [expandedChunks, setExpandedChunks] = useState<Set<number>>(new Set())

  // Group chunks by chapter
  const chunksByChapter = useMemo(() => {
    const grouped: Record<string, Chunk[]> = {}
    for (const chunk of chunks) {
      const chapter = chunk.chapter_title || 'Unknown Chapter'
      if (!grouped[chapter]) {
        grouped[chapter] = []
      }
      grouped[chapter].push(chunk)
    }
    return grouped
  }, [chunks])

  // Filter chunks based on search
  const filteredChunks = useMemo(() => {
    if (!searchQuery.trim()) return chunks
    const query = searchQuery.toLowerCase()
    return chunks.filter(chunk => 
      chunk.text.toLowerCase().includes(query) ||
      chunk.chapter_title.toLowerCase().includes(query)
    )
  }, [chunks, searchQuery])

  // Stats
  const totalTokens = useMemo(() => 
    chunks.reduce((sum, c) => sum + c.token_count, 0),
    [chunks]
  )

  const toggleExpand = (index: number) => {
    setExpandedChunks(prev => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex-1 flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-indigo-600 mx-auto animate-spin mb-2" />
          <p className="text-gray-500">Loading chunks...</p>
        </div>
      </div>
    )
  }

  if (!chunks.length) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex-1 flex items-center justify-center">
        <div className="text-center">
          <FileText className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="font-semibold text-gray-900 mb-1">No Chunks Found</h3>
          <p className="text-sm text-gray-500">This document doesn't have any indexed chunks</p>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex-1 flex flex-col min-h-0">
      {/* Header with stats and search */}
      <div className="px-4 py-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-sm">
            <Hash className="w-4 h-4 text-gray-400" />
            <span className="font-medium text-gray-900">{chunks.length}</span>
            <span className="text-gray-500">chunks</span>
          </div>
          <div className="w-px h-4 bg-gray-300" />
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium text-gray-900">{totalTokens.toLocaleString()}</span>
            <span className="text-gray-500">tokens</span>
          </div>
          <div className="w-px h-4 bg-gray-300" />
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium text-gray-900">{Object.keys(chunksByChapter).length}</span>
            <span className="text-gray-500">chapters</span>
          </div>
        </div>
        
        {/* Search */}
        <div className="relative">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search chunks..."
            className="pl-9 pr-4 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 w-64"
          />
        </div>
      </div>

      {/* Chunks list */}
      <div className="flex-1 overflow-y-auto scrollbar-custom">
        <div className="p-4 space-y-2">
          {filteredChunks.map((chunk, idx) => {
            const isExpanded = expandedChunks.has(idx)
            const isSelected = selectedChunkIndex === idx
            const previewText = chunk.text.slice(0, 200) + (chunk.text.length > 200 ? '...' : '')
            
            return (
              <div
                key={chunk.id}
                className={`border rounded-lg transition-all ${
                  isSelected 
                    ? 'border-indigo-300 bg-indigo-50' 
                    : 'border-gray-200 hover:border-gray-300 bg-white'
                }`}
              >
                {/* Chunk header */}
                <div 
                  className="px-4 py-3 flex items-start justify-between gap-4 cursor-pointer"
                  onClick={() => {
                    setSelectedChunkIndex(isSelected ? null : idx)
                    toggleExpand(idx)
                  }}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-mono bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                        #{chunk.chunk_index}
                      </span>
                      <span className="text-sm font-medium text-gray-900 truncate">
                        {chunk.chapter_title}
                      </span>
                    </div>
                    {!isExpanded && (
                      <p className="text-sm text-gray-600 line-clamp-2">
                        {previewText}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    <span className="text-xs text-gray-500">
                      {chunk.token_count} tokens
                    </span>
                    {isExpanded ? (
                      <ChevronUp className="w-4 h-4 text-gray-400" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-gray-400" />
                    )}
                  </div>
                </div>
                
                {/* Expanded content */}
                {isExpanded && (
                  <div className="px-4 pb-4 pt-0">
                    <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-700 whitespace-pre-wrap font-mono leading-relaxed max-h-96 overflow-y-auto scrollbar-custom">
                      {chunk.text}
                    </div>
                    <div className="mt-2 flex items-center gap-4 text-xs text-gray-500">
                      <span>ID: {chunk.id}</span>
                      <span>{chunk.text.length} characters</span>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          
          {filteredChunks.length === 0 && searchQuery && (
            <div className="text-center py-8 text-gray-500">
              <Search className="w-8 h-8 mx-auto mb-2 text-gray-300" />
              <p>No chunks match "{searchQuery}"</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default ChunksView

