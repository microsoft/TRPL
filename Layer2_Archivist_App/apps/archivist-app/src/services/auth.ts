// Authentication Service for Azure App Service Easy Auth

import { isExpired } from "react-jwt";

export interface UserInfo {
  user_id: string;
  email: string;
  name: string;
  display_name: string;
  isAdmin?: boolean;
  isDataFoundations?: boolean;
  isArchivist?: boolean;
  groups?: string[];
}

export interface AuthResponse {
  authenticated: boolean;
  user: UserInfo;
}

interface EasyAuthMeResponse {
  access_token?: string;
  id_token?: string;
  user_claims?: Array<{ typ: string; val: string }>;
}

// Cache for tokens
let cachedAccessToken: string | null = null;
let cachedIdToken: string | null = null;

/**
 * Fetch tokens from Azure App Service Easy Auth /.auth/me endpoint
 */
async function fetchTokens(): Promise<{ accessToken: string | null; idToken: string | null }> {
  if (import.meta.env.DEV) {
    return { accessToken: null, idToken: null };
  }

  try {
    const response = await fetch('/.auth/me', { credentials: 'include' });
    if (!response.ok) {
      console.warn('Failed to get tokens from /.auth/me');
      return { accessToken: null, idToken: null };
    }

    const authData: EasyAuthMeResponse[] = await response.json();
    if (authData && authData.length > 0) {
      return {
        accessToken: authData[0].access_token || null,
        idToken: authData[0].id_token || null,
      };
    }
    return { accessToken: null, idToken: null };
  } catch (error) {
    console.error('Error fetching tokens:', error);
    return { accessToken: null, idToken: null };
  }
}

/**
 * Refresh tokens if expired
 */
async function refreshTokensIfNeeded(): Promise<void> {
  if (cachedAccessToken && isExpired(cachedAccessToken)) {
    try {
      await fetch('/.auth/refresh', { credentials: 'include' });
      cachedAccessToken = null;
      cachedIdToken = null;
    } catch (error) {
      console.warn('Failed to refresh tokens');
    }
  }
}

/**
 * Get access token (user claims: email, name, oid)
 */
export async function getAccessToken(): Promise<string | null> {
  if (import.meta.env.DEV) return null;
  
  await refreshTokensIfNeeded();
  
  if (cachedAccessToken && !isExpired(cachedAccessToken)) {
    return cachedAccessToken;
  }

  const { accessToken, idToken } = await fetchTokens();
  cachedAccessToken = accessToken;
  cachedIdToken = idToken;
  return cachedAccessToken;
}

/**
 * Get ID token (contains groups)
 */
export async function getIdToken(): Promise<string | null> {
  if (import.meta.env.DEV) return null;
  
  await refreshTokensIfNeeded();
  
  if (cachedIdToken && !isExpired(cachedIdToken)) {
    return cachedIdToken;
  }

  const { accessToken, idToken } = await fetchTokens();
  cachedAccessToken = accessToken;
  cachedIdToken = idToken;
  return cachedIdToken;
}

/**
 * Clear cached tokens
 */
export function clearTokens(): void {
  cachedAccessToken = null;
  cachedIdToken = null;
}

// Backwards compatibility
export const clearAccessToken = clearTokens;

/**
 * Get current authenticated user from backend
 */
export async function getCurrentUser(): Promise<UserInfo | null> {
  try {
    const baseUrl = import.meta.env.VITE_BACKEND_API_ENDPOINT || '';
    
    // Get both tokens
    const accessToken = await getAccessToken();
    const idToken = await getIdToken();
    
    const headers: Record<string, string> = {
      'Accept': 'application/json',
    };
    
    // Send access token for user claims
    if (accessToken) {
      headers['Authorization'] = `Bearer ${accessToken}`;
    }
    
    // Send ID token for groups
    if (idToken) {
      headers['X-ID-Token'] = idToken;
    }
    
    const response = await fetch(`${baseUrl}/api/v1/auth/me`, {
      credentials: 'include',
      headers,
    });

    if (!response.ok) {
      if (response.status === 401) {
        return null;
      }
      throw new Error(`Failed to get user info: ${response.statusText}`);
    }

    const data: AuthResponse = await response.json();
    return data.user;
  } catch (error) {
    console.error('Error fetching user info:', error);
    if (import.meta.env.DEV) {
      console.warn('Using mock user for development');
      return {
        user_id: 'dev-user-12345',
        email: 'dev.user@trpl.local',
        name: 'Development User',
        display_name: 'Development User',
        isAdmin: true,
        isDataFoundations: true,
        isArchivist: true,
        groups: [],
      };
    }
    return null;
  }
}

/**
 * Check if user is authenticated
 */
export async function isAuthenticated(): Promise<boolean> {
  const user = await getCurrentUser();
  return user !== null;
}

/**
 * Sign out
 */
export function signOut(): void {
  clearTokens();
  window.location.href = '/.auth/logout';
}

/**
 * Get login URL
 */
export function getLoginUrl(): string {
  return '/.auth/login/aad';
}
