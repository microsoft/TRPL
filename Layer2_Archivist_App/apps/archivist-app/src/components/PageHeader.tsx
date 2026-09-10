import React, { ReactNode } from 'react'

interface PageHeaderProps {
  title: string | ReactNode
  subtitle?: string
  description?: string // Alias for subtitle for backward compatibility
  stats?: unknown // Allow stats prop but ignore it for now
  className?: string
  children?: ReactNode
}

const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  subtitle,
  description,
  stats, // Accept but ignore stats prop
  className = '',
  children,
}) => {
  // Use description as fallback to subtitle for backward compatibility
  const subtitleText = subtitle || description

  return (
    <div className={`bg-museum-green border-b border-gray-200 px-6 py-4 shadow-sm ${className}`}>
      <div className="max-w-8xl mx-auto">
        <div className="flex items-center justify-between gap-4">
          <div className="text-left">
            {typeof title === 'string' ? (
              <h1 className="text-2xl font-bold text-white">{title}</h1>
            ) : (
              <div className="text-2xl font-bold text-white">{title}</div>
            )}
            {subtitleText && (
              <p className="mt-1 text-sm text-white/80">{subtitleText}</p>
            )}
          </div>
          {children && (
            <div className="flex items-center gap-3">{children}</div>
          )}
        </div>
      </div>
    </div>
  )
}

export default PageHeader
