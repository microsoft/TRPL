'use client'

import { useEffect } from 'react'

interface KeyboardShortcutsConfig {
  onSearch?: () => void
  onSelectAll?: () => void
  onExport?: () => void
  onOpenItem?: () => void
  onEscape?: () => void
  'cmd+s'?: () => void
  'cmd+shift+c'?: () => void
  'alt+shift+f'?: () => void
  'cmd+i'?: () => void
  'cmd+t'?: () => void
}

export const useKeyboardShortcuts = (config: KeyboardShortcutsConfig) => {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Cmd/Ctrl + K for search
      if ((event.metaKey || event.ctrlKey) && event.key === 'k' && config.onSearch) {
        event.preventDefault()
        config.onSearch()
      }
      
      // Cmd/Ctrl + A for select all (only when not in input field)
      if ((event.metaKey || event.ctrlKey) && event.key === 'a' && config.onSelectAll) {
        const target = event.target as HTMLElement
        if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
          event.preventDefault()
          config.onSelectAll()
        }
      }
      
      // Cmd/Ctrl + E for export
      if ((event.metaKey || event.ctrlKey) && event.key === 'e' && config.onExport) {
        event.preventDefault()
        config.onExport()
      }
      
      // Cmd/Ctrl + S for save
      if ((event.metaKey || event.ctrlKey) && event.key === 's' && config['cmd+s']) {
        event.preventDefault()
        config['cmd+s']()
      }
      
      // Cmd/Ctrl + Shift + C for save and complete
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key === 'C' && config['cmd+shift+c']) {
        event.preventDefault()
        config['cmd+shift+c']()
      }
      
      // Alt + Shift + F for toggle severe deviation
      if (event.altKey && event.shiftKey && event.key === 'F' && config['alt+shift+f']) {
        event.preventDefault()
        config['alt+shift+f']()
      }
      
      // Cmd/Ctrl + I for import AI text
      if ((event.metaKey || event.ctrlKey) && event.key === 'i' && config['cmd+i']) {
        event.preventDefault()
        config['cmd+i']()
      }
      
      // Cmd/Ctrl + T for focus transcription
      if ((event.metaKey || event.ctrlKey) && event.key === 't' && config['cmd+t']) {
        event.preventDefault()
        config['cmd+t']()
      }
      
      // Enter to open item (only when not in input field)
      if (event.key === 'Enter' && config.onOpenItem) {
        const target = event.target as HTMLElement
        if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA' && target.tagName !== 'BUTTON') {
          event.preventDefault()
          config.onOpenItem()
        }
      }
      
      // ESC to close modals
      if (event.key === 'Escape' && config.onEscape) {
        config.onEscape()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [config])
}
