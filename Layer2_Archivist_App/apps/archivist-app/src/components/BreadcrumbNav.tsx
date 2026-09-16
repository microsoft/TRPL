// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import React from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'

interface BreadcrumbItem {
  label: string
  href?: string
  current?: boolean
}

// Friendly names for known base paths
const routeNames: Record<string, string> = {
  '': 'Home',
  'collections': 'Collections',
  'repositories': 'Repositories',
  'review': 'Review',
  'admin': 'Admin',
  'tools': 'Tools',
  'data-pipeline': 'Data Pipeline',
  'statistics': 'Statistics',
  'field-mappings': 'Identifier Field Mapping',
  'search': 'Search',
  'settings': 'Settings',
  'users': 'Users',
  'roles': 'Roles',
  'audit-logs': 'Audit Logs',
  'ingestion': 'Ingestion',
  'import': 'Import',
  'export': 'Export',
  'digital-resources': 'Public Resources',
  'public-resources-ingestion': 'Public Ingestion',
  'cyclopedia': 'Cyclopedia',
  'moore-chronology': 'Moore Chronology',
  'chronologies': 'Chronologies',
  'genealogy-papers': 'Genealogy & Papers',
  'letter': 'Letter',
}

// Helper function to format a URL segment into a readable label
const formatSegmentLabel = (segment: string): string => {
  // Check if we have a predefined friendly name
  if (routeNames[segment]) {
    return routeNames[segment]
  }
  
  // Convert hyphenated/underscored segments to Title Case
  return segment
    .split(/[-_]/)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
}

const BreadcrumbNav: React.FC = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const pathname = location.pathname || '/'
  
  // Split URL into segments
  const segments = pathname.split('/').filter(Boolean)
  
  // Build breadcrumbs starting with Home
  const breadcrumbs: BreadcrumbItem[] = []
  
  // Always start with Home
  breadcrumbs.push({ 
    label: 'Home', 
    href: '/', 
    current: segments.length === 0 
  })
  
  // Build breadcrumbs from URL segments
  segments.forEach((segment, index) => {
    const isLast = index === segments.length - 1
    const prevSegment = index > 0 ? segments[index - 1] : null
    const nextSegment = index < segments.length - 1 ? segments[index + 1] : null
    
    // Check if this looks like a UUID (record ID) - typically 36 chars with hyphens
    const looksLikeUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(segment)
    
    // If this is a UUID and it's the last segment, add "Review" breadcrumb before it
    if (looksLikeUUID && isLast) {
      // Add "Review" breadcrumb that links to the full path including UUID
      const reviewHref = '/' + segments.slice(0, index + 1).join('/')
      breadcrumbs.push({
        label: 'Review',
        href: undefined,
        current: false // Make it clickable
      })
      // Then add the UUID itself as the last breadcrumb
      breadcrumbs.push({
        label: segment,
        href: undefined,
        current: true // This is the current page
      })
      return
    }
    
    // Skip UUID segments that aren't last (shouldn't happen, but just in case)
    if (looksLikeUUID) {
      return
    }
    
    // Skip the "review" segment - we'll add "Review" before the UUID instead
    if (segment === 'review') {
      return
    }
    
    // Try to decode the segment (for repository/collection names)
    let label: string
    try {
      const decoded = decodeURIComponent(segment)
      // Use friendly name if available, otherwise format the decoded segment
      label = formatSegmentLabel(decoded)
    } catch {
      label = formatSegmentLabel(segment)
    }
    
    // Special handling: if this is a repository name (after 'repositories' segment),
    // and 'collections' comes after it, link to the collections page
    let href: string | undefined
    if (isLast) {
      href = undefined // Last item is current, no href
    } else if (segment === 'admin' || segment === 'tools' || segment === 'errors') {
      // Admin and Tools are not navigable routes by themselves - they're parent categories
      href = undefined
    } else if (prevSegment === 'repositories' && nextSegment === 'collections') {
      // Repository name should link to its collections page
      href = '/' + segments.slice(0, index + 2).join('/') // Include 'collections' in href
    } else {
      // Normal href building
      href = '/' + segments.slice(0, index + 1).join('/')
    }
    
    breadcrumbs.push({
      label,
      href,
      current: isLast
    })
  })

  const handleBreadcrumbClick = (item: BreadcrumbItem) => {
    if (item.href && !item.current) {
      navigate(item.href)
    }
  }

  // For use in Header (dark background)
  const isInHeader = true // This will be passed as a prop in the future if needed

  return (
    <nav className="flex items-center space-x-2 text-base" aria-label="Breadcrumb">
      {breadcrumbs.map((item, index) => (
        <div key={index} className="flex items-center space-x-2">
          {index > 0 && (
            <ChevronRight className="w-3 h-3 text-gray-400" aria-hidden="true" />
          )}
          {item.href && !item.current ? (
            <span
              className="text-gray-300 hover:text-white hover:underline cursor-pointer transition-all duration-200"
              onClick={() => handleBreadcrumbClick(item)}
              role="button"
              tabIndex={0}
            >
              {item.label}
            </span>
          ) : (
            <span className="text-white font-semibold cursor-default">
              {item.label}
            </span>
          )}
        </div>
      ))}
    </nav>
  )
}

export default BreadcrumbNav
