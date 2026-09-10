import React, { createContext, useContext, useState, useEffect, useMemo, ReactNode } from 'react';
import { getCurrentUser, UserInfo } from '@/services/auth';

/**
 * Permission model (roles can be combined - permissions are OR'd together):
 * - Admin: Full access to everything (including EPUB processing)
 * - DataFoundations: Full access to Pipeline Monitor
 * - Archivist: Full access to Data Ingestion, Documents, Repositories
 * 
 * When a user has multiple roles (e.g., DataFoundations + Archivist), 
 * they get combined permissions from both roles.
 * 
 * Note: EPUB processing is Admin-only for all roles.
 */
interface Permissions {
  // Pipeline Monitor page
  pipelineMonitor: { canView: boolean; canEdit: boolean };
  // Data Ingestion page
  dataIngestion: { canView: boolean; canEdit: boolean };
  // Documents/Records pages
  documents: { canView: boolean; canEdit: boolean };
  // EPUB Management
  epub: { canView: boolean; canEdit: boolean };
  // Repositories/Collections
  repositories: { canView: boolean; canEdit: boolean };
}

interface AuthContextType {
  user: UserInfo | null;
  isAuthenticated: boolean;
  // Role flags
  isAdmin: boolean;
  isDataFoundations: boolean;
  isArchivist: boolean;
  // True if user has at least one role assigned
  hasAnyRole: boolean;
  // Computed permissions by page
  permissions: Permissions;
  // Loading state
  isLoading: boolean;
  error: string | null;
  refreshUser: () => Promise<void>;
}

const defaultPermissions: Permissions = {
  pipelineMonitor: { canView: false, canEdit: false },
  dataIngestion: { canView: false, canEdit: false },
  documents: { canView: false, canEdit: false },
  epub: { canView: false, canEdit: false },
  repositories: { canView: false, canEdit: false },
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadUser = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const userInfo = await getCurrentUser();
      setUser(userInfo);
    } catch (err) {
      console.error('Failed to load user:', err);
      setError(err instanceof Error ? err.message : 'Failed to load user');
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadUser();
  }, []);

  const refreshUser = async () => {
    await loadUser();
  };

  // Extract role flags
  const isAdmin = user?.isAdmin ?? false;
  const isDataFoundations = user?.isDataFoundations ?? false;
  const isArchivist = user?.isArchivist ?? false;
  
  // Check if user has any role assigned
  const hasAnyRole = isAdmin || isDataFoundations || isArchivist;

  // Compute permissions based on roles
  // When user has multiple roles, permissions are combined (OR logic - if ANY role grants access)
  const permissions = useMemo<Permissions>(() => {
    if (!user) return defaultPermissions;

    // Admin: Full access to everything
    if (isAdmin) {
      return {
        pipelineMonitor: { canView: true, canEdit: true },
        dataIngestion: { canView: true, canEdit: true },
        documents: { canView: true, canEdit: true },
        epub: { canView: true, canEdit: true }, // Only Admin can process EPUB
        repositories: { canView: true, canEdit: true },
      };
    }

    // For non-admin users, combine permissions from all roles they have
    // Start with base read-only permissions
    const combined: Permissions = {
      pipelineMonitor: { canView: true, canEdit: false },
      dataIngestion: { canView: true, canEdit: false },
      documents: { canView: true, canEdit: false },
      epub: { canView: true, canEdit: false }, // EPUB processing: Admin only
      repositories: { canView: true, canEdit: false },
    };

    // DataFoundations: Full access to Pipeline Monitor
    if (isDataFoundations) {
      combined.pipelineMonitor.canEdit = true;
    }

    // Archivist: Full access to Data Ingestion, Documents, Repositories
    if (isArchivist) {
      combined.dataIngestion.canEdit = true;
      combined.documents.canEdit = true;
      combined.repositories.canEdit = true;
    }

    return combined;
  }, [user, isAdmin, isDataFoundations, isArchivist]);

  const value: AuthContextType = {
    user,
    isAuthenticated: user !== null,
    isAdmin,
    isDataFoundations,
    isArchivist,
    hasAnyRole,
    permissions,
    isLoading,
    error,
    refreshUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

