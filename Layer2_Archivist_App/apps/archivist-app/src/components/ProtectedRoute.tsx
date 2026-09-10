import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'

type PermissionKey = 'pipelineMonitor' | 'dataIngestion' | 'documents' | 'epub' | 'repositories'

interface ProtectedRouteProps {
  children: React.ReactNode
  /** Permission key to check for canEdit access */
  permission?: PermissionKey
  /** Only allow admin users */
  adminOnly?: boolean
  /** Redirect path when access denied (default: '/') */
  redirectTo?: string
}

/**
 * Protects routes based on user permissions.
 * Redirects to home page if user doesn't have required access.
 */
export default function ProtectedRoute({ 
  children, 
  permission, 
  adminOnly = false,
  redirectTo = '/'
}: ProtectedRouteProps) {
  const { isAdmin, permissions, isLoading } = useAuth()
  const location = useLocation()

  // Show nothing while loading auth state
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600" />
      </div>
    )
  }

  // Build redirect URL with access denied message
  const buildRedirectUrl = () => {
    const params = new URLSearchParams()
    params.set('access_denied', 'true')
    params.set('from', location.pathname)
    return `${redirectTo}?${params.toString()}`
  }

  // Check admin-only routes
  if (adminOnly && !isAdmin) {
    return <Navigate to={buildRedirectUrl()} replace />
  }

  // Check permission-based routes (requires canEdit)
  if (permission && !permissions[permission].canEdit) {
    return <Navigate to={buildRedirectUrl()} replace />
  }

  return <>{children}</>
}
