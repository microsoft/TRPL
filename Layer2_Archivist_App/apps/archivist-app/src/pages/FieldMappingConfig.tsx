'use client'

import React, { useState, useEffect, useCallback } from 'react'
import { 
  Plus, 
  Edit2, 
  Trash2, 
  Save, 
  X, 
  Database,
  Layers,
  Tag,
  RefreshCw
} from 'lucide-react'
import { apiService, FieldMapping } from '@/services/api'
import Toast from '@/components/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'
import PageHeader from '@/components/PageHeader'

interface EditableMapping {
  id?: string
  repository: string
  collection: string
  identifier_field: string
  display_name: string
  isNew?: boolean
}

const FieldMappingConfigPage: React.FC = () => {
  const [mappings, setMappings] = useState<FieldMapping[]>([])
  const [repositories, setRepositories] = useState<string[]>([])
  const [collections, setCollections] = useState<string[]>([])
  const [metadataFields, setMetadataFields] = useState<string[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null)
  const [editingMapping, setEditingMapping] = useState<EditableMapping | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<{ isOpen: boolean; mapping: FieldMapping | null }>({
    isOpen: false,
    mapping: null
  })

  // Load initial data
  const loadData = useCallback(async () => {
    setIsLoading(true)
    try {
      // Load mappings
      const mappingsRes = await apiService.getFieldMappings()
      setMappings(mappingsRes.mappings || [])
      
      // Load repositories
      try {
        const reposRes = await apiService.getRepositoryNames()
        setRepositories(reposRes.repositories || [])
      } catch (error) {
        console.error('Failed to load repositories:', error)
        setRepositories([])
      }
      
      // Load metadata fields
      try {
        const fieldsRes = await apiService.getAvailableMetadataFields()
        setMetadataFields(fieldsRes.fields || [])
      } catch (error) {
        console.error('Failed to load metadata fields:', error)
        // Provide default fields if API fails
        setMetadataFields([
          'Source Record ID', 'Identifier', 'Title', 'Description', 'Creator', 'Collection',
          'Repository', 'Resource Type', 'Creation Date', 'Period',
          'Language', 'record_id', 'source_record_id'
        ])
      }
    } catch (error) {
      console.error('Failed to load data:', error)
      setToast({
        type: 'error',
        message: 'Failed to load field mapping data'
      })
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Load collections when repository changes
  const loadCollections = useCallback(async (repository: string) => {
    if (!repository) {
      setCollections([])
      return
    }
    try {
      const response = await apiService.getCollections(repository)
      setCollections(response.collections || [])
    } catch (error) {
      console.error('Failed to load collections:', error)
      setCollections([])
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  useEffect(() => {
    if (editingMapping?.repository) {
      loadCollections(editingMapping.repository)
    }
  }, [editingMapping?.repository, loadCollections])

  // Handle creating new mapping
  const handleNewMapping = () => {
    setEditingMapping({
      repository: '',
      collection: '',
      identifier_field: 'Source Record ID',
      display_name: '',
      isNew: true
    })
    setCollections([])
  }

  // Handle editing existing mapping
  const handleEditMapping = (mapping: FieldMapping) => {
    setEditingMapping({
      id: mapping.id,
      repository: mapping.repository,
      collection: mapping.collection || '',
      identifier_field: mapping.identifier_field,
      display_name: mapping.display_name || '',
      isNew: false
    })
    loadCollections(mapping.repository)
  }

  // Handle saving mapping
  const handleSaveMapping = async () => {
    if (!editingMapping) return
    
    if (!editingMapping.repository) {
      setToast({ type: 'error', message: 'Please select a repository' })
      return
    }
    
    if (!editingMapping.identifier_field) {
      setToast({ type: 'error', message: 'Please select an identifier field' })
      return
    }

    setIsSaving(true)
    try {
      if (editingMapping.isNew) {
        await apiService.createFieldMapping({
          repository: editingMapping.repository,
          collection: editingMapping.collection || undefined,
          identifier_field: editingMapping.identifier_field,
          display_name: editingMapping.display_name || undefined
        })
        setToast({ type: 'success', message: 'Field mapping created successfully' })
      } else if (editingMapping.id) {
        await apiService.updateFieldMapping(editingMapping.id, {
          identifier_field: editingMapping.identifier_field,
          display_name: editingMapping.display_name || undefined
        })
        setToast({ type: 'success', message: 'Field mapping updated successfully' })
      }
      
      setEditingMapping(null)
      await loadData()
    } catch (error) {
      console.error('Failed to save mapping:', error)
      setToast({
        type: 'error',
        message: error instanceof Error ? error.message : 'Failed to save field mapping'
      })
    } finally {
      setIsSaving(false)
    }
  }

  // Handle deleting mapping
  const handleDeleteMapping = async () => {
    if (!deleteConfirm.mapping) return

    try {
      await apiService.deleteFieldMapping(deleteConfirm.mapping.id)
      setToast({ type: 'success', message: 'Field mapping deleted successfully' })
      setDeleteConfirm({ isOpen: false, mapping: null })
      await loadData()
    } catch (error) {
      console.error('Failed to delete mapping:', error)
      setToast({
        type: 'error',
        message: error instanceof Error ? error.message : 'Failed to delete field mapping'
      })
    }
  }

  // Format date for display
  const formatDate = (dateString: string) => {
    if (!dateString) return '—'
    try {
      const date = new Date(dateString)
      return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return '—'
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <PageHeader
        title="Identifier Field Mapping"
        subtitle="Configure which metadata field to use as the Identifier for each repository or collection"
      />

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-6 py-6">
        {/* Action Bar */}
        <div className="flex justify-end mb-6">
          <button
            onClick={handleNewMapping}
            className="flex items-center gap-2 px-4 py-2 bg-museum-green text-white rounded-lg hover:bg-museum-green/90 transition-colors font-medium"
          >
            <Plus className="w-5 h-5" />
            Add Mapping
          </button>
        </div>

        {/* Loading State */}
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <RefreshCw className="w-8 h-8 text-museum-green animate-spin" />
            <span className="ml-3 text-gray-600">Loading field mappings...</span>
          </div>
        ) : (
          <>
            {/* Edit/Create Form */}
            {editingMapping && (
              <div className="bg-white rounded-lg shadow-md border border-gray-200 mb-6">
                <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                  <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                    {editingMapping.isNew ? (
                      <>
                        <Plus className="w-5 h-5 text-museum-green" />
                        Create New Field Mapping
                      </>
                    ) : (
                      <>
                        <Edit2 className="w-5 h-5 text-museum-green" />
                        Edit Field Mapping
                      </>
                    )}
                  </h2>
                </div>
                <div className="p-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Repository Selection */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        <Database className="w-4 h-4 inline mr-1" />
                        Repository <span className="text-red-500">*</span>
                      </label>
                      <select
                        value={editingMapping.repository}
                        onChange={(e) => {
                          setEditingMapping({ ...editingMapping, repository: e.target.value, collection: '' })
                          loadCollections(e.target.value)
                        }}
                        disabled={!editingMapping.isNew}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-museum-accent focus:border-museum-accent disabled:bg-gray-100"
                      >
                        <option value="">Select a repository...</option>
                        {repositories.map((repo) => (
                          <option key={repo} value={repo}>{repo}</option>
                        ))}
                      </select>
                    </div>

                    {/* Collection Selection (Optional) */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        <Layers className="w-4 h-4 inline mr-1" />
                        Collection <span className="text-gray-400">(Optional)</span>
                      </label>
                      <select
                        value={editingMapping.collection}
                        onChange={(e) => setEditingMapping({ ...editingMapping, collection: e.target.value })}
                        disabled={!editingMapping.isNew || !editingMapping.repository}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-museum-accent focus:border-museum-accent disabled:bg-gray-100"
                      >
                        <option value="">All collections (repository-level)</option>
                        {collections.map((col) => (
                          <option key={col} value={col}>{col}</option>
                        ))}
                      </select>
                    </div>

                    {/* Identifier Field Selection */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        <Tag className="w-4 h-4 inline mr-1" />
                        Identifier Field <span className="text-red-500">*</span>
                      </label>
                      <select
                        value={editingMapping.identifier_field}
                        onChange={(e) => setEditingMapping({ ...editingMapping, identifier_field: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-museum-accent focus:border-museum-accent"
                      >
                        {metadataFields.map((field) => (
                          <option key={field} value={field}>{field}</option>
                        ))}
                      </select>
                    </div>

                    {/* Display Name (Optional) */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        Display Name <span className="text-gray-400">(Optional)</span>
                      </label>
                      <input
                        type="text"
                        value={editingMapping.display_name}
                        onChange={(e) => setEditingMapping({ ...editingMapping, display_name: e.target.value })}
                        placeholder="Custom column header (defaults to 'Source Record ID')"
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-museum-accent focus:border-museum-accent"
                      />
                    </div>
                  </div>

                  {/* Form Actions */}
                  <div className="flex items-center justify-end gap-3 mt-6 pt-6 border-t border-gray-200">
                    <button
                      onClick={() => setEditingMapping(null)}
                      className="px-4 py-2 text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <X className="w-4 h-4 inline mr-1" />
                      Cancel
                    </button>
                    <button
                      onClick={handleSaveMapping}
                      disabled={isSaving || !editingMapping.repository || !editingMapping.identifier_field}
                      className="px-4 py-2 bg-museum-green text-white rounded-lg hover:bg-museum-green/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                      {isSaving ? (
                        <>
                          <RefreshCw className="w-4 h-4 animate-spin" />
                          Saving...
                        </>
                      ) : (
                        <>
                          <Save className="w-4 h-4" />
                          Save Mapping
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Mappings Table */}
            <div className="bg-white rounded-lg shadow-md border border-gray-200">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-semibold text-gray-900">
                  Configured Field Mappings
                  <span className="ml-2 text-sm font-normal text-gray-500">
                    ({mappings.length} {mappings.length === 1 ? 'mapping' : 'mappings'})
                  </span>
                </h2>
              </div>

              {mappings.length === 0 ? (
                <div className="px-6 py-12 text-center">
                  <Database className="w-12 h-12 text-gray-300 mx-auto mb-4" />
                  <h3 className="text-lg font-medium text-gray-900 mb-2">No Field Mappings Configured</h3>
                  <p className="text-gray-500 mb-4">
                    Create a field mapping to customize which metadata field is displayed as the Identifier.
                  </p>
                  <button
                    onClick={handleNewMapping}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-museum-green text-white rounded-lg hover:bg-museum-green/90 transition-colors"
                  >
                    <Plus className="w-5 h-5" />
                    Add First Mapping
                  </button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">
                          Repository
                        </th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">
                          Collection
                        </th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">
                          Identifier Field
                        </th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">
                          Display Name
                        </th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">
                          Created
                        </th>
                        <th className="px-6 py-3 text-right text-xs font-semibold text-gray-600 uppercase tracking-wider">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {mappings.map((mapping) => (
                        <tr key={mapping.id} className="hover:bg-gray-50">
                          <td className="px-6 py-4">
                            <div className="flex items-center gap-2">
                              <Database className="w-4 h-4 text-gray-400" />
                              <span className="font-medium text-gray-900">{mapping.repository}</span>
                            </div>
                          </td>
                          <td className="px-6 py-4">
                            {mapping.collection ? (
                              <div className="flex items-center gap-2">
                                <Layers className="w-4 h-4 text-gray-400" />
                                <span className="text-gray-700">{mapping.collection}</span>
                              </div>
                            ) : (
                              <span className="text-gray-400 italic">All collections</span>
                            )}
                          </td>
                          <td className="px-6 py-4">
                            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                              <Tag className="w-3 h-3 mr-1" />
                              {mapping.identifier_field}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-gray-600">
                            {mapping.display_name || <span className="text-gray-400">—</span>}
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-500">
                            <div>{formatDate(mapping.created_at)}</div>
                            {mapping.created_by && (
                              <div className="text-xs text-gray-400">by {mapping.created_by}</div>
                            )}
                          </td>
                          <td className="px-6 py-4 text-right">
                            <div className="flex items-center justify-end gap-2">
                              <button
                                onClick={() => handleEditMapping(mapping)}
                                className="p-2 text-gray-500 hover:text-museum-green hover:bg-gray-100 rounded-lg transition-colors"
                                title="Edit mapping"
                              >
                                <Edit2 className="w-4 h-4" />
                              </button>
                              <button
                                onClick={() => setDeleteConfirm({ isOpen: true, mapping })}
                                className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                                title="Delete mapping"
                              >
                                <Trash2 className="w-4 h-4" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={deleteConfirm.isOpen}
        onClose={() => setDeleteConfirm({ isOpen: false, mapping: null })}
        onConfirm={handleDeleteMapping}
        title="Delete Field Mapping"
        message={deleteConfirm.mapping ? 
          `Are you sure you want to delete the field mapping for "${deleteConfirm.mapping.repository}"${deleteConfirm.mapping.collection ? ` / "${deleteConfirm.mapping.collection}"` : ''}? This action cannot be undone.` 
          : ''
        }
        confirmText="Delete"
        cancelText="Cancel"
        variant="danger"
      />

      {/* Toast Notification */}
      {toast && (
        <div className="fixed top-20 right-6 z-50">
          <Toast
            type={toast.type}
            message={toast.message}
            onClose={() => setToast(null)}
          />
        </div>
      )}
    </div>
  )
}

export default FieldMappingConfigPage
