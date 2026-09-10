import React, { useMemo, useState, useCallback } from 'react'
import { 
  BookOpen, 
  FileText, 
  ChevronDown, 
  ChevronRight 
} from 'lucide-react'
import type { Section, FlattenedSection, SectionGroup } from './types'

interface SectionListProps {
  sections: Section[]
  expandedSections: Set<number>
  onToggleSection: (order: number) => void
  onToggleExpand: (order: number) => void
  onSelectAll: () => void
  onDeselectAll: () => void
}

const getSectionIcon = (type: string) => {
  switch (type) {
    case 'chapter': return <BookOpen className="w-4 h-4" />
    default: return <FileText className="w-4 h-4" />
  }
}

const getSectionBadgeColor = (type: string) => {
  switch (type) {
    case 'chapter': return 'bg-blue-100 text-blue-800'
    case 'prologue': return 'bg-purple-100 text-purple-800'
    case 'epilogue': return 'bg-green-100 text-green-800'
    case 'foreword': return 'bg-amber-100 text-amber-800'
    case 'afterword': return 'bg-teal-100 text-teal-800'
    case 'introduction': return 'bg-indigo-100 text-indigo-800'
    default: return 'bg-gray-100 text-gray-800'
  }
}

const SectionList: React.FC<SectionListProps> = ({
  sections,
  expandedSections,
  onToggleSection,
  onToggleExpand,
}) => {
  // Expanded groups state - start with all_sections expanded
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(['all_sections']))

  // Flatten all sections (including children) for display with level info
  const flattenedSections = useMemo(() => {
    const result: FlattenedSection[] = []
    
    const flatten = (secs: Section[], level: number = 0) => {
      for (const section of secs) {
        result.push({ ...section, displayLevel: level })
        if (section.children && section.children.length > 0) {
          flatten(section.children, level + 1)
        }
      }
    }
    
    flatten(sections)
    return result
  }, [sections])

  // Show all sections in a single list
  const groupedSections = useMemo((): SectionGroup[] => {
    const groups: SectionGroup[] = [
      {
        key: 'all_sections',
        label: 'All Sections',
        icon: <BookOpen className="w-4 h-4" />,
        bgColor: 'bg-blue-500',
        sections: flattenedSections
      }
    ]
    return groups.filter(g => g.sections.length > 0)
  }, [flattenedSections])

  const toggleGroup = useCallback((groupKey: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev)
      if (next.has(groupKey)) {
        next.delete(groupKey)
      } else {
        next.add(groupKey)
      }
      return next
    })
  }, [])

  const toggleGroupSelection = useCallback((sections: FlattenedSection[]) => {
    const allSelected = sections.every(s => s.selected)
    sections.forEach(s => {
      if (s.selected === allSelected) {
        onToggleSection(s.order)
      }
    })
  }, [onToggleSection])

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex-1 flex flex-col min-h-0">
      <div className="flex-1 overflow-y-auto scrollbar-custom">
        {groupedSections.map((group) => {
          const isGroupExpanded = expandedGroups.has(group.key)
          const groupSelectedCount = group.sections.filter(s => s.selected).length
          const allSelected = groupSelectedCount === group.sections.length
          const someSelected = groupSelectedCount > 0 && !allSelected

          return (
            <div key={group.key} className="border-b border-gray-200 last:border-b-0">
              {/* Group Header */}
              <div
                className="flex items-center gap-3 p-3 bg-gray-50 hover:bg-gray-100 cursor-pointer transition-colors"
                onClick={() => toggleGroup(group.key)}
              >
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = someSelected
                  }}
                  onChange={(e) => {
                    e.stopPropagation()
                    toggleGroupSelection(group.sections)
                  }}
                  onClick={(e) => e.stopPropagation()}
                  className="checkbox-round"
                />
                <button className="p-1 text-gray-500">
                  {isGroupExpanded ? (
                    <ChevronDown className="w-4 h-4" />
                  ) : (
                    <ChevronRight className="w-4 h-4" />
                  )}
                </button>
                <div className={`w-7 h-7 ${group.bgColor} rounded flex items-center justify-center`}>
                  <span className="text-white">{group.icon}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-semibold text-gray-900">{group.label}</p>
                    <span className="px-2 py-0.5 text-xs bg-gray-200 text-gray-700 rounded-full">
                      {group.sections.length} {group.sections.length === 1 ? 'section' : 'sections'}
                    </span>
                    <span className="text-xs text-gray-500">
                      ({groupSelectedCount} selected)
                    </span>
                  </div>
                </div>
              </div>

              {/* Group Sections - Flattened with indentation */}
              {isGroupExpanded && (
                <div className="bg-white">
                  {group.sections.map((section) => {
                    const indentLevel = section.displayLevel || 0
                    const paddingLeft = 12 + (indentLevel * 20)
                    
                    return (
                      <div
                        key={section.order}
                        className={`border-t border-gray-100 ${
                          section.selected ? 'bg-indigo-50/30' : 'bg-white'
                        } ${indentLevel > 0 ? 'border-l-2 border-l-indigo-200' : ''}`}
                        style={{ marginLeft: indentLevel > 0 ? `${indentLevel * 16}px` : 0 }}
                      >
                        <div 
                          className="flex items-center gap-3 p-2.5"
                          style={{ paddingLeft: `${paddingLeft}px` }}
                        >
                          <input
                            type="checkbox"
                            checked={section.selected}
                            onChange={() => onToggleSection(section.order)}
                            className="checkbox-round"
                          />
                          {/* Only show chevron if section has children or content */}
                          {(section.children && section.children.length > 0) || section.content ? (
                            <button
                              onClick={() => onToggleExpand(section.order)}
                              className="p-1 text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0"
                            >
                              {expandedSections.has(section.order) ? (
                                <ChevronDown className="w-4 h-4" />
                              ) : (
                                <ChevronRight className="w-4 h-4" />
                              )}
                            </button>
                          ) : (
                            <div className="w-6 h-6 flex-shrink-0" />
                          )}
                          <div className={`p-1 rounded flex-shrink-0 ${getSectionBadgeColor(section.type)}`}>
                            {getSectionIcon(section.type)}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              {indentLevel > 0 && (
                                <span className="text-xs text-gray-400 font-mono">└</span>
                              )}
                              <p className={`font-medium text-gray-900 truncate ${indentLevel > 0 ? 'text-sm' : ''}`}>
                                {section.title}
                              </p>
                              {indentLevel === 0 && (
                                <span className={`px-1.5 py-0.5 text-xs rounded ${getSectionBadgeColor(section.type)}`}>
                                  {section.type}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                        
                        {/* Expanded Content Preview */}
                        {expandedSections.has(section.order) && section.content && (
                          <div className="px-4 pb-3" style={{ paddingLeft: `${paddingLeft + 40}px` }}>
                            <div className="bg-gray-50 rounded-lg p-3 max-h-32 overflow-y-auto">
                              <p className="text-sm text-gray-700 whitespace-pre-wrap line-clamp-6">
                                {section.content.substring(0, 500)}
                                {section.content.length > 500 && '...'}
                              </p>
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default SectionList

