// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React, { useState } from 'react'
import { FileText, Cog, Tags, Smile, GripVertical, Edit, Trash2, Plus, MoreVertical } from 'lucide-react'

interface VocabularyTerm {
  id: string
  name: string
  count: number
}

interface VocabularyCategory {
  id: string
  name: string
  description: string
  icon: React.ReactNode
  color: string
  terms: VocabularyTerm[]
}

interface VocabularyManagementProps {
  onShowConfirmation: () => void
}

const VocabularyManagement: React.FC<VocabularyManagementProps> = ({ onShowConfirmation }) => {
  const [newTerm, setNewTerm] = useState('')
  const [activeCategory, setActiveCategory] = useState<string | null>(null)

  const vocabularyCategories: VocabularyCategory[] = [
    {
      id: 'resource-types',
      name: 'Resource Types',
      description: 'Document and media classifications',
      icon: <FileText className="w-5 h-5 text-blue-600" />,
      color: 'blue',
      terms: [
        { id: '1', name: 'correspondence', count: 847 },
        { id: '2', name: 'official_document', count: 523 },
        { id: '3', name: 'news_article', count: 234 },
        { id: '4', name: 'photograph', count: 156 },
        { id: '5', name: 'speech', count: 89 },
        { id: '6', name: 'diary', count: 67 },
      ]
    },
    {
      id: 'production-methods',
      name: 'Production Methods',
      description: 'How documents were created',
      icon: <Cog className="w-5 h-5 text-green-600" />,
      color: 'green',
      terms: [
        { id: '1', name: 'handwritten', count: 432 },
        { id: '2', name: 'typed', count: 387 },
        { id: '3', name: 'printed', count: 298 },
        { id: '4', name: 'photograph', count: 156 },
      ]
    },
    {
      id: 'document-topics',
      name: 'Document Topics',
      description: 'Subject matter classifications',
      icon: <Tags className="w-5 h-5 text-purple-600" />,
      color: 'purple',
      terms: [
        { id: '1', name: 'conservation', count: 312 },
        { id: '2', name: 'foreign_policy', count: 287 },
        { id: '3', name: 'domestic_policy', count: 234 },
        { id: '4', name: 'panama_canal', count: 189 },
        { id: '5', name: 'trust_busting', count: 145 },
        { id: '6', name: 'rough_riders', count: 98 },
      ]
    },
    {
      id: 'document-sentiment',
      name: 'Document Sentiment',
      description: 'Emotional tone classifications',
      icon: <Smile className="w-5 h-5 text-amber-600" />,
      color: 'amber',
      terms: [
        { id: '1', name: 'positive', count: 678 },
        { id: '2', name: 'neutral', count: 543 },
        { id: '3', name: 'negative', count: 368 },
      ]
    }
  ]

  const handleAddTerm = (categoryId: string) => {
    if (newTerm.trim()) {
      // In a real app, this would make an API call
      console.log('Adding term:', newTerm, 'to category:', categoryId)
      setNewTerm('')
    }
  }

  const handleEditTerm = (termId: string, categoryId: string) => {
    console.log('Editing term:', termId, 'in category:', categoryId)
  }

  const handleDeleteTerm = (termId: string, categoryId: string) => {
    console.log('Deleting term:', termId, 'from category:', categoryId)
    onShowConfirmation()
  }

  const getColorClasses = (color: string) => {
    const colorMap = {
      blue: 'bg-blue-100 text-blue-600',
      green: 'bg-green-100 text-green-600',
      purple: 'bg-purple-100 text-purple-600',
      amber: 'bg-amber-100 text-amber-600',
    }
    return colorMap[color as keyof typeof colorMap] || 'bg-gray-100 text-gray-600'
  }

  const getButtonColor = (color: string) => {
    const colorMap = {
      blue: 'bg-blue-600 hover:bg-blue-700',
      green: 'bg-green-600 hover:bg-green-700',
      purple: 'bg-purple-600 hover:bg-purple-700',
      amber: 'bg-amber-600 hover:bg-amber-700',
    }
    return colorMap[color as keyof typeof colorMap] || 'bg-gray-600 hover:bg-gray-700'
  }

  return (
    <section id="vocabulary-management" className="bg-museum-50 px-6 py-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h3 className="text-xl font-semibold text-museum-900 mb-2">Controlled Vocabularies</h3>
          <p className="text-museum-600">Manage standardized terms used for metadata classification and search faceting</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {vocabularyCategories.slice(0, 3).map((category) => (
            <div key={category.id} className="bg-white rounded-xl shadow-sm border border-museum-200">
              <div className="p-6 border-b border-museum-200">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center space-x-3">
                    <div className={`w-8 h-8 ${getColorClasses(category.color)} rounded-lg flex items-center justify-center`}>
                      {category.icon}
                    </div>
                    <div>
                      <h4 className="font-semibold text-museum-900">{category.name}</h4>
                      <p className="text-sm text-museum-500">{category.description}</p>
                    </div>
                  </div>
                  <div className="flex items-center space-x-2">
                    <span className="text-sm text-museum-500">{category.terms.length} terms</span>
                    <button className="p-2 text-museum-500 hover:text-museum-700 hover:bg-museum-100 rounded-lg transition-colors">
                      <MoreVertical className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                <div className="space-y-2 max-h-64 overflow-y-auto custom-scrollbar">
                  {category.terms.map((term) => (
                    <div key={term.id} className="flex items-center justify-between p-3 bg-museum-50 rounded-lg">
                      <div className="flex items-center space-x-3">
                        <GripVertical className="w-4 h-4 text-museum-400" />
                        <span className="text-sm font-medium text-museum-900">{term.name}</span>
                      </div>
                      <div className="flex items-center space-x-2">
                        <span className="text-xs text-museum-500 bg-museum-200 px-2 py-1 rounded">{term.count} items</span>
                        <button 
                          className="text-museum-500 hover:text-museum-700"
                          onClick={() => handleEditTerm(term.id, category.id)}
                        >
                          <Edit className="w-3 h-3" />
                        </button>
                        <button 
                          className="text-red-500 hover:text-red-700"
                          onClick={() => handleDeleteTerm(term.id, category.id)}
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="p-4 bg-museum-50">
                <div className="flex items-center space-x-2">
                  <input
                    type="text"
                    placeholder={`Add new ${category.name.toLowerCase().replace(' ', ' ')}...`}
                    className="flex-1 px-3 py-2 border border-museum-300 rounded-lg focus:ring-2 focus:ring-museum-500 focus:border-transparent text-sm"
                    value={activeCategory === category.id ? newTerm : ''}
                    onChange={(e) => setNewTerm(e.target.value)}
                    onFocus={() => setActiveCategory(category.id)}
                  />
                  <button 
                    className={`px-4 py-2 ${getButtonColor(category.color)} text-white rounded-lg transition-colors flex items-center space-x-2`}
                    onClick={() => handleAddTerm(category.id)}
                  >
                    <Plus className="w-4 h-4" />
                    <span>Add</span>
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Document Sentiment - Special case */}
        <div className="mt-8">
          <div className="bg-white rounded-xl shadow-sm border border-museum-200 max-w-md">
            <div className="p-6 border-b border-museum-200">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center space-x-3">
                  <div className="w-8 h-8 bg-amber-100 rounded-lg flex items-center justify-center">
                    <Smile className="w-5 h-5 text-amber-600" />
                  </div>
                  <div>
                    <h4 className="font-semibold text-museum-900">Document Sentiment</h4>
                    <p className="text-sm text-museum-500">Emotional tone classifications</p>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <span className="text-sm text-museum-500">3 terms</span>
                  <button className="p-2 text-museum-500 hover:text-museum-700 hover:bg-museum-100 rounded-lg transition-colors">
                    <MoreVertical className="w-4 h-4" />
                  </button>
                </div>
              </div>
              <div className="space-y-2">
                {vocabularyCategories[3].terms.map((term) => (
                  <div key={term.id} className="flex items-center justify-between p-3 bg-museum-50 rounded-lg">
                    <div className="flex items-center space-x-3">
                      <GripVertical className="w-4 h-4 text-museum-400" />
                      <span className="text-sm font-medium text-museum-900">{term.name}</span>
                    </div>
                    <div className="flex items-center space-x-2">
                      <span className="text-xs text-museum-500 bg-museum-200 px-2 py-1 rounded">{term.count} items</span>
                      <button 
                        className="text-museum-500 hover:text-museum-700"
                        onClick={() => handleEditTerm(term.id, vocabularyCategories[3].id)}
                      >
                        <Edit className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="p-4 bg-museum-50">
              <p className="text-sm text-museum-500 text-center italic">Sentiment vocabulary is fixed to maintain consistency</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default VocabularyManagement
