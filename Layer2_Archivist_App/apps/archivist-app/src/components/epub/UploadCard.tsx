// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useRef, useCallback } from 'react'
import { Upload, FileText, CheckCircle2, Loader2 } from 'lucide-react'

interface UploadCardProps {
  epubFile: File | null
  opfFile: File | null
  isUploading: boolean
  disabled?: boolean
  onEpubSelect: (file: File) => void
  onOpfSelect: (file: File) => void
  onUpload: () => void
  onFilesDropped: (files: File[]) => void
}

const UploadCard: React.FC<UploadCardProps> = ({
  epubFile,
  opfFile,
  isUploading,
  disabled = false,
  onEpubSelect,
  onOpfSelect,
  onUpload,
  onFilesDropped
}) => {
  const isDisabled = disabled || isUploading
  const epubInputRef = useRef<HTMLInputElement>(null)
  const opfInputRef = useRef<HTMLInputElement>(null)

  const handleEpubChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) onEpubSelect(file)
  }, [onEpubSelect])

  const handleOpfChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) onOpfSelect(file)
  }, [onOpfSelect])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const files = Array.from(e.dataTransfer.files)
    onFilesDropped(files)
  }, [onFilesDropped])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
  }, [])

  return (
    <div 
      onDrop={isDisabled ? undefined : handleDrop}
      onDragOver={isDisabled ? undefined : handleDragOver}
      className={`bg-white rounded-lg shadow-sm border border-gray-200 p-3 ${isDisabled ? 'opacity-60' : ''}`}
    >
      <div className="flex items-center gap-2 mb-2">
        <Upload className="w-4 h-4 text-indigo-600" />
        <span className="text-sm font-semibold text-gray-900">Upload Files</span>
      </div>
      
      <div className="flex gap-2 mb-2">
        {/* EPUB */}
        <div 
          className={`flex-1 min-w-0 p-2 rounded border transition-all text-center overflow-hidden ${
            isDisabled ? 'cursor-not-allowed bg-gray-50' :
            epubFile ? 'border-green-300 bg-green-50 cursor-pointer' : 'border-gray-200 hover:border-indigo-300 cursor-pointer'
          }`}
          onClick={() => !isDisabled && epubInputRef.current?.click()}
        >
          {epubFile ? (
            <CheckCircle2 className="w-4 h-4 text-green-600 mx-auto" />
          ) : (
            <FileText className="w-4 h-4 text-gray-400 mx-auto" />
          )}
          <p className="text-xs mt-1 truncate" title={epubFile?.name}>
            {epubFile ? epubFile.name : '.epub'}
          </p>
        </div>
        
        {/* OPF */}
        <div 
          className={`flex-1 min-w-0 p-2 rounded border transition-all text-center overflow-hidden ${
            isDisabled ? 'cursor-not-allowed bg-gray-50' :
            opfFile ? 'border-green-300 bg-green-50 cursor-pointer' : 'border-gray-200 hover:border-indigo-300 cursor-pointer'
          }`}
          onClick={() => !isDisabled && opfInputRef.current?.click()}
        >
          {opfFile ? (
            <CheckCircle2 className="w-4 h-4 text-green-600 mx-auto" />
          ) : (
            <FileText className="w-4 h-4 text-gray-400 mx-auto" />
          )}
          <p className="text-xs mt-1 truncate" title={opfFile?.name}>
            {opfFile ? opfFile.name : '.opf'}
          </p>
        </div>
      </div>
      
      <input 
        ref={epubInputRef} 
        type="file" 
        accept=".epub" 
        onChange={handleEpubChange} 
        className="hidden" 
        disabled={isDisabled} 
      />
      <input 
        ref={opfInputRef} 
        type="file" 
        accept=".opf" 
        onChange={handleOpfChange} 
        className="hidden" 
        disabled={isDisabled} 
      />
      
      <button
        onClick={onUpload}
        disabled={!epubFile || !opfFile || isDisabled}
        title={disabled ? 'Admin access required' : undefined}
        className="w-full py-2 bg-indigo-600 text-white text-sm font-medium rounded hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-1.5"
      >
        {isUploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
        {isUploading ? 'Uploading...' : 'Upload'}
      </button>
    </div>
  )
}

export default UploadCard

