// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { useState, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Archive, Home, BarChart3, Settings, ChevronDown, ChevronRight, Wrench, BookOpen, Activity, Workflow, HelpCircle, TableProperties, Database, MessageSquareText, Library, Upload } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'

interface SidebarProps {
  isOpen: boolean
  onClose: () => void
}

type PermissionKey = 'pipelineMonitor' | 'dataIngestion' | 'documents' | 'epub' | 'repositories'

interface NavChild {
  name: string
  path: string
  icon: React.ComponentType<{ className?: string }>
  permission?: PermissionKey  // Permission key to check canView
  adminOnly?: boolean         // Only show for admins
}

interface NavItem {
  name: string
  path?: string
  icon: React.ComponentType<{ className?: string }>
  permission?: PermissionKey
  adminOnly?: boolean
  children?: NavChild[]
}

const Sidebar: React.FC<SidebarProps> = ({ isOpen, onClose }) => {
  const navigate = useNavigate()
  const location = useLocation()
  const { isAdmin, permissions } = useAuth()
  const [expandedItems, setExpandedItems] = useState<string[]>(['Admin', 'Tools', 'Help'])

  const allNavigationItems: NavItem[] = [
    {
      name: 'Dashboard',
      path: '/',
      icon: Home,
    },
    {
      name: 'Repositories',
      path: '/repositories',
      icon: Archive,
      // No permission check - repositories are viewable by everyone
    },
    {
      name: 'Public Resources',
      path: '/digital-resources',
      icon: Library,
    },
    {
      name: 'Correction requests',
      path: '/correction-requests',
      icon: MessageSquareText,
      permission: 'documents',
    },
    {
      name: 'Tools',
      icon: Wrench,
      children: [
        {
          name: 'Public Ingestion',
          path: '/tools/public-resources-ingestion',
          icon: Upload,
          adminOnly: true,
        },
        {
          name: 'EPUB Processor',
          path: '/tools/epub-processor',
          icon: BookOpen,
          adminOnly: true,  // Only admin can process EPUB
        },
        {
          name: 'Data Pipeline',
          path: '/tools/data-pipeline',
          icon: Activity,
          permission: 'pipelineMonitor',
        },
        {
          name: 'Data Ingestion',
          path: '/tools/data-ingestion',
          icon: Database,
          permission: 'dataIngestion',
        },
      ],
    },
    {
      name: 'Admin',
      icon: Settings,
      children: [
        {
          name: 'Statistics',
          path: '/admin/statistics',
          icon: BarChart3,
          // Accessible to everyone - anyone can rebuild stats
        },
      ],
    },
  ]

  // Filter navigation items based on permissions
  // Show items only if user has edit access (can actually do something useful)
  const navigationItems = useMemo(() => {
    const filterItem = (item: NavItem): NavItem | null => {
      // Check admin-only items
      if (item.adminOnly && !isAdmin) {
        return null
      }

      // Check permission-based items - show if user can edit (not just view)
      if (item.permission && !permissions[item.permission].canEdit) {
        return null
      }

      // Filter children if present
      if (item.children) {
        const filteredChildren = item.children.filter(child => {
          if (child.adminOnly && !isAdmin) {
            return false
          }
          // Show child if user can edit the associated feature
          if (child.permission && !permissions[child.permission].canEdit) {
            return false
          }
          return true
        })

        // If no children left after filtering, hide the parent group
        if (filteredChildren.length === 0) {
          return null
        }

        return { ...item, children: filteredChildren }
      }

      return item
    }

    return allNavigationItems
      .map(filterItem)
      .filter((item): item is NavItem => item !== null)
  }, [isAdmin, permissions])

  const handleNavigation = (path: string) => {
    navigate(path)
    onClose()
  }

  const toggleExpand = (name: string) => {
    setExpandedItems(prev => 
      prev.includes(name) 
        ? prev.filter(item => item !== name)
        : [...prev, name]
    )
  }

  const isActive = (path: string) => {
    if (path === '/') {
      return location.pathname === '/'
    }
    if (path === '/repositories') {
      return location.pathname.startsWith('/repositories')
    }
    if (path === '/digital-resources') {
      return location.pathname.startsWith('/digital-resources')
    }
    return location.pathname === path
  }

  const isParentActive = (item: NavItem) => {
    if (item.children) {
      return item.children.some(child => location.pathname === child.path)
    }
    return false
  }

  return (
    <aside
      className={`shadow-lg transition-all duration-300 ease-in-out shrink-0 ${
        isOpen ? 'w-64 border-r border-gray-100' : 'w-0'
      } ${isOpen ? '' : 'pointer-events-none'}`}
    >
      <div className={`flex flex-col h-full w-64 bg-gray-100 ${isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
        {/* Navigation Items */}
        <nav className="flex-1 p-4 space-y-2 bg-gray-100 h-full">
          {navigationItems.map((item) => {
            const Icon = item.icon
            const hasChildren = item.children && item.children.length > 0
            const isExpanded = expandedItems.includes(item.name)
            const active = item.path ? isActive(item.path) : isParentActive(item)

            if (hasChildren) {
              return (
                <div key={item.name}>
                  <button
                    onClick={() => toggleExpand(item.name)}
                    className={`w-full flex items-center justify-between px-4 py-3 rounded-lg transition-colors ${
                      active
                        ? 'bg-museum-accent/10 text-museum-accent'
                        : 'text-gray-700 hover:bg-gray-200'
                    }`}
                  >
                    <div className="flex items-center space-x-3">
                      <Icon className="w-5 h-5" />
                      <span className="font-medium">{item.name}</span>
                    </div>
                    {isExpanded ? (
                      <ChevronDown className="w-4 h-4" />
                    ) : (
                      <ChevronRight className="w-4 h-4" />
                    )}
                  </button>
                  {isExpanded && (
                    <div className="ml-4 mt-1 space-y-1">
                      {item.children!.map((child) => {
                        const ChildIcon = child.icon
                        const childActive = isActive(child.path)
                        return (
                          <button
                            key={child.path}
                            onClick={() => handleNavigation(child.path)}
                            className={`w-full flex items-center space-x-3 px-4 py-2.5 rounded-lg transition-colors ${
                              childActive
                                ? 'bg-museum-accent text-white'
                                : 'text-gray-600 hover:bg-gray-200'
                            }`}
                          >
                            <ChildIcon className="w-4 h-4" />
                            <span className="font-medium text-sm">{child.name}</span>
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            }

            return (
              <button
                key={item.path}
                onClick={() => handleNavigation(item.path!)}
                className={`w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-colors ${
                  active
                    ? 'bg-museum-accent text-white'
                    : 'text-gray-700 hover:bg-gray-200'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="font-medium">{item.name}</span>
              </button>
            )
          })}
        </nav>
      </div>
    </aside>
  )
}

export default Sidebar

