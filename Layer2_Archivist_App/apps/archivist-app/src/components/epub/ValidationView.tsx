import React, { useState, useMemo } from 'react'
import { 
  Loader2, 
  GitCompare, 
  Plus, 
  Minus, 
  CheckCircle2,
  Type,
  Code
} from 'lucide-react'
import * as Diff from 'diff'
import type { Section, DiffViewMode, FilterConfig } from './types'

interface ValidationViewProps {
  originalSections: Section[]
  filteredSections: Section[]
  selectedSectionOrder: number | null
  isLoading: boolean
  filterConfig: FilterConfig | null
  filterDefaults: FilterConfig | null
  onSelectSection: (order: number) => void
}

// Helper to flatten sections recursively
const flattenSections = (sections: Section[]): Section[] => {
  const result: Section[] = []
  for (const s of sections) {
    result.push(s)
    if (s.children) result.push(...flattenSections(s.children))
  }
  return result
}

const ValidationView: React.FC<ValidationViewProps> = ({
  originalSections,
  filteredSections,
  selectedSectionOrder,
  isLoading,
  filterConfig,
  filterDefaults,
  onSelectSection
}) => {
  const [diffViewMode, setDiffViewMode] = useState<DiffViewMode>('text')

  const flatOriginal = useMemo(() => flattenSections(originalSections), [originalSections])
  const flatFiltered = useMemo(() => flattenSections(filteredSections), [filteredSections])
  
  // Show all selected sections, even if no content was extracted
  const selectableSections = useMemo(() => 
    flatOriginal.filter(s => s.selected), 
    [flatOriginal]
  )

  const selectedOrigSection = useMemo(() => 
    flatOriginal.find(s => s.order === selectedSectionOrder),
    [flatOriginal, selectedSectionOrder]
  )

  const selectedFiltSection = useMemo(() => 
    flatFiltered.find(s => s.order === selectedSectionOrder),
    [flatFiltered, selectedSectionOrder]
  )

  const origText = selectedOrigSection?.content || ''
  const filtText = selectedFiltSection?.content || ''

  // Compute word-level diff
  const { wordDiff, addedWords, removedWords, hasChanges } = useMemo(() => {
    const diff = Diff.diffWords(origText, filtText)
    let added = 0
    let removed = 0
    diff.forEach(part => {
      const words = part.value.trim().split(/\s+/).filter(w => w).length
      if (part.added) added += words
      if (part.removed) removed += words
    })
    return {
      wordDiff: diff,
      addedWords: added,
      removedWords: removed,
      hasChanges: added > 0 || removed > 0
    }
  }, [origText, filtText])

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex-1 flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-indigo-600 mx-auto animate-spin mb-2" />
          <p className="text-gray-500">Loading extracted text...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex-1 flex flex-col min-h-0">
      <div className="flex flex-1 min-h-0">
        {/* Left Nav - Selected Sections */}
        <div className="w-72 border-r border-gray-200 overflow-y-auto scrollbar-custom bg-gray-50 flex-shrink-0">
          <div className="px-3 py-2 border-b border-gray-200 bg-white sticky top-0 z-10">
            <span className="font-semibold text-gray-900 text-sm">
              Sections ({selectableSections.length})
            </span>
          </div>
          <div className="p-1">
            {selectableSections.map(section => {
              const hasContent = Boolean(section.content)
              return (
                <button
                  key={section.order}
                  onClick={() => onSelectSection(section.order)}
                  className={`w-full text-left px-3 py-2 rounded mb-0.5 transition-all ${
                    selectedSectionOrder === section.order
                      ? 'bg-indigo-100 text-indigo-900'
                      : 'hover:bg-gray-100 text-gray-700'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <p className={`text-sm flex-1 ${!hasContent ? 'text-amber-600' : ''}`}>
                      {section.title}
                    </p>
                    {!hasContent && (
                      <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded flex-shrink-0">
                        No content
                      </span>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
        
        {/* Right Content - Diff View */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {selectedSectionOrder !== null && selectedOrigSection ? (
            <>
              {/* File-style header with view toggle */}
              <div className="bg-gray-800 text-gray-100 px-4 py-2 text-sm flex items-center justify-between flex-shrink-0">
                <div className="flex items-center gap-3">
                  <GitCompare className="w-4 h-4 text-gray-400 flex-shrink-0" />
                  <span className="font-medium">{selectedOrigSection.title}</span>
                </div>
                <div className="flex items-center gap-4 text-xs flex-shrink-0">
                  {/* View Mode Toggle */}
                  <div className="flex items-center bg-gray-700 rounded-lg p-0.5">
                    <button
                      onClick={() => setDiffViewMode('text')}
                      className={`px-2 py-1 rounded flex items-center gap-1 transition-colors ${
                        diffViewMode === 'text' ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'
                      }`}
                    >
                      <Type className="w-3 h-3" />
                      Text
                    </button>
                    <button
                      onClick={() => setDiffViewMode('html')}
                      className={`px-2 py-1 rounded flex items-center gap-1 transition-colors ${
                        diffViewMode === 'html' ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'
                      }`}
                    >
                      <Code className="w-3 h-3" />
                      HTML
                    </button>
                  </div>
                  {!origText && !filtText ? (
                    <span className="text-amber-400 flex items-center gap-1">
                      No content extracted
                    </span>
                  ) : hasChanges ? (
                    <>
                      <span className="flex items-center gap-1 text-green-400">
                        <Plus className="w-3.5 h-3.5" />
                        {addedWords} words
                      </span>
                      <span className="flex items-center gap-1 text-red-400">
                        <Minus className="w-3.5 h-3.5" />
                        {removedWords} words
                      </span>
                    </>
                  ) : (
                    <span className="text-green-400 flex items-center gap-1">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      No changes
                    </span>
                  )}
                </div>
              </div>
              
              {!origText && !filtText ? (
                <div className="flex-1 flex flex-col items-center justify-center text-gray-500 bg-amber-50">
                  <div className="text-center p-6">
                    <div className="w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center mx-auto mb-3">
                      <Code className="w-6 h-6 text-amber-600" />
                    </div>
                    <p className="font-medium text-amber-800">No content extracted for this section</p>
                    <p className="text-sm text-amber-600 mt-1">
                      The section may not have a valid href or the content could not be found in the EPUB
                    </p>
                  </div>
                </div>
              ) : diffViewMode === 'text' ? (
                <TextDiffView 
                  wordDiff={wordDiff} 
                  hasChanges={hasChanges} 
                  filtText={filtText} 
                />
              ) : (
                <HtmlView 
                  htmlContent={selectedOrigSection.html_content || ''} 
                  filterConfig={filterConfig}
                  filterDefaults={filterDefaults}
                />
              )}
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-gray-500 bg-gray-50">
              <GitCompare className="w-12 h-12 text-gray-300 mb-3" />
              <p className="font-medium">Select a section to view diff</p>
              <p className="text-sm text-gray-400 mt-1">Compare original and filtered content</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// Text Diff View Component
interface TextDiffViewProps {
  wordDiff: Diff.Change[]
  hasChanges: boolean
  filtText: string
}

const TextDiffView: React.FC<TextDiffViewProps> = ({ wordDiff, hasChanges, filtText }) => {
  return (
    <>
      {/* Legend */}
      <div className="bg-gray-100 px-4 py-1.5 border-b border-gray-200 flex items-center gap-6 text-xs flex-shrink-0">
        <span className="flex items-center gap-1.5">
          <span className="px-1.5 py-0.5 rounded bg-red-200 text-red-800 line-through text-xs">removed</span>
          <span className="text-gray-500">Filtered out</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="px-1.5 py-0.5 rounded bg-green-200 text-green-800 text-xs">added</span>
          <span className="text-gray-500">New in filtered</span>
        </span>
        <span className="text-gray-400 ml-auto">
          Only changed words are highlighted
        </span>
      </div>
      
      {/* Diff Content */}
      <div className="flex-1 overflow-y-auto scrollbar-custom bg-white">
        <div className="p-6">
          {hasChanges ? (
            <div className="prose prose-sm max-w-none leading-relaxed text-gray-800">
              {wordDiff.map((part, i) => {
                const text = part.value
                
                if (part.added) {
                  return text.split('\n').map((line, lineIdx) => (
                    <React.Fragment key={`${i}-${lineIdx}`}>
                      {lineIdx > 0 && <br />}
                      {line && (
                        <span className="bg-green-200 text-green-900 px-0.5 rounded-sm border-b-2 border-green-400">
                          {line}
                        </span>
                      )}
                    </React.Fragment>
                  ))
                } else if (part.removed) {
                  return text.split('\n').map((line, lineIdx) => (
                    <React.Fragment key={`${i}-${lineIdx}`}>
                      {lineIdx > 0 && <br />}
                      {line && (
                        <span className="bg-red-200 text-red-900 px-0.5 rounded-sm line-through border-b-2 border-red-400">
                          {line}
                        </span>
                      )}
                    </React.Fragment>
                  ))
                } else {
                  return text.split('\n').map((line, lineIdx) => (
                    <React.Fragment key={`${i}-${lineIdx}`}>
                      {lineIdx > 0 && <br />}
                      {line}
                    </React.Fragment>
                  ))
                }
              })}
            </div>
          ) : (
            <div>
              <div className="flex items-center gap-2 mb-4 text-green-600">
                <CheckCircle2 className="w-5 h-5" />
                <span className="font-medium">No differences - showing filtered text</span>
              </div>
              <div className="prose prose-sm max-w-none leading-relaxed text-gray-800">
                {filtText.split('\n').map((line, i) => (
                  <React.Fragment key={i}>
                    {i > 0 && <br />}
                    {line}
                  </React.Fragment>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// HTML View Component
interface HtmlViewProps {
  htmlContent: string
  filterConfig: FilterConfig | null
  filterDefaults: FilterConfig | null
}

const HtmlView: React.FC<HtmlViewProps> = ({ htmlContent, filterConfig, filterDefaults }) => {
  return (
    <>
      {/* Legend */}
      <div className="bg-amber-50 px-4 py-1.5 border-b border-amber-200 flex items-center gap-4 text-xs flex-shrink-0">
        <span className="flex items-center gap-1.5 text-amber-700">
          <Code className="w-3.5 h-3.5" />
          <span className="font-medium">HTML Source View</span>
        </span>
        <span className="text-amber-600">
          Identify tags and classes to filter (e.g., &lt;figcaption&gt;, class="caption")
        </span>
      </div>
      
      {/* HTML Content */}
      <div className="flex-1 overflow-y-auto scrollbar-custom bg-gray-900">
        <pre className="p-4 text-sm font-mono text-gray-300 whitespace-pre-wrap break-all">
          <code>
            {(() => {
              const parts = htmlContent.split(/(<[^>]+>)/g)
              return parts.map((part, i) => {
                if (part.startsWith('<')) {
                  // Check if it's a tag we're filtering
                  const tagMatch = part.match(/<\/?(\w+)/)
                  const tagName = tagMatch?.[1]?.toLowerCase()
                  const isFiltered = filterConfig?.exclude_tags.includes(tagName || '') ||
                    filterDefaults?.exclude_tags.includes(tagName || '')
                  
                  // Check for filtered classes
                  const classMatch = part.match(/class=["']([^"']+)["']/)
                  const classes = classMatch?.[1]?.split(' ') || []
                  const hasFilteredClass = classes.some(cls => 
                    filterConfig?.exclude_classes.includes(cls) ||
                    filterDefaults?.exclude_classes.includes(cls) ||
                    filterConfig?.class_patterns.some(p => cls.includes(p)) ||
                    filterDefaults?.class_patterns.some(p => cls.includes(p))
                  )
                  
                  // Check for filtered IDs
                  const idMatch = part.match(/id=["']([^"']+)["']/)
                  const hasFilteredId = idMatch && (
                    filterConfig?.exclude_ids.includes(idMatch[1]) ||
                    filterDefaults?.exclude_ids.includes(idMatch[1])
                  )
                  
                  if (isFiltered || hasFilteredClass || hasFilteredId) {
                    return (
                      <span key={i} className="bg-red-900/50 text-red-300 rounded px-0.5" title="This element is filtered">
                        {part}
                      </span>
                    )
                  }
                  return (
                    <span key={i} className="text-cyan-400">
                      {part}
                    </span>
                  )
                }
                return <span key={i}>{part}</span>
              })
            })()}
          </code>
        </pre>
      </div>
    </>
  )
}

export default ValidationView

