'use client'

import React from 'react'
import { AlertTriangle } from 'lucide-react'

interface ConfirmationModalProps {
  onClose: () => void
  onConfirm: () => void
  title?: string
  message?: string
  confirmText?: string
  cancelText?: string
}

const ConfirmationModal: React.FC<ConfirmationModalProps> = ({
  onClose,
  onConfirm,
  title = 'Confirm Action',
  message = 'Are you sure you want to delete this vocabulary term? This will affect all items using this classification.',
  confirmText = 'Delete',
  cancelText = 'Cancel'
}) => {
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl w-full max-w-md mx-4">
        <div className="p-6">
          <div className="flex items-center space-x-3 mb-4">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-museum-900">{title}</h3>
              <p className="text-sm text-museum-500">This action cannot be undone</p>
            </div>
          </div>
          <p className="text-museum-700 mb-6">{message}</p>
          <div className="flex items-center space-x-3">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 border border-museum-300 text-museum-700 rounded-lg hover:bg-museum-50 transition-colors"
            >
              {cancelText}
            </button>
            <button
              onClick={onConfirm}
              className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
            >
              {confirmText}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default ConfirmationModal
