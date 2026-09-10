'use client'

import { X } from 'lucide-react'

interface KeyboardShortcutsModalProps {
  isOpen: boolean
  onClose: () => void
}

const KeyboardShortcutsModal: React.FC<KeyboardShortcutsModalProps> = ({ isOpen, onClose }) => {
  if (!isOpen) return null

  const shortcuts = [
    { action: 'Open search', key: '⌘K' },
    { action: 'Next page', key: '→' },
    { action: 'Previous page', key: '←' },
    { action: 'Select all', key: '⌘A' },
    { action: 'Export selected', key: '⌘E' },
    { action: 'Open item', key: 'Enter' },
  ]

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl w-full max-w-2xl mx-4 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between p-6 border-b border-museum-200">
          <h3 className="text-xl font-semibold text-museum-900">Keyboard Shortcuts</h3>
          <button
            className="p-2 text-museum-500 hover:text-museum-700 hover:bg-museum-100 rounded-lg transition-colors"
            onClick={onClose}
            aria-label="Close shortcuts modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <h4 className="font-medium text-museum-900 mb-3">Navigation</h4>
              <div className="space-y-2">
                {shortcuts.slice(0, 3).map((shortcut, index) => (
                  <div key={index} className="flex items-center justify-between">
                    <span className="text-sm text-museum-600">{shortcut.action}</span>
                    <kbd className="px-2 py-1 bg-museum-100 rounded text-xs font-medium">
                      {shortcut.key}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h4 className="font-medium text-museum-900 mb-3">Actions</h4>
              <div className="space-y-2">
                {shortcuts.slice(3).map((shortcut, index) => (
                  <div key={index} className="flex items-center justify-between">
                    <span className="text-sm text-museum-600">{shortcut.action}</span>
                    <kbd className="px-2 py-1 bg-museum-100 rounded text-xs font-medium">
                      {shortcut.key}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default KeyboardShortcutsModal
