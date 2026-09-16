// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Azure Application Insights configuration for React app.
 * 
 * Tracks:
 * - Page views (automatic with React Router)
 * - User interactions
 * - Exceptions
 * - Custom events
 * - Performance metrics
 */
import { ApplicationInsights } from '@microsoft/applicationinsights-web';
import { ReactPlugin } from '@microsoft/applicationinsights-react-js';

// React plugin for automatic component tracking
const reactPlugin = new ReactPlugin();

// Application Insights instance
let appInsights: ApplicationInsights | null = null;

/**
 * Initialize Application Insights.
 * Call this once at app startup (main.tsx).
 */
export function initializeAppInsights(): ApplicationInsights | null {
  const connectionString = import.meta.env.VITE_APPLICATIONINSIGHTS_CONNECTION_STRING;
  
  if (!connectionString) {
    console.warn(
      'Application Insights not configured. Set VITE_APPLICATIONINSIGHTS_CONNECTION_STRING environment variable.'
    );
    return null;
  }

  try {
    appInsights = new ApplicationInsights({
      config: {
        connectionString,
        extensions: [reactPlugin],
        enableAutoRouteTracking: true, // Track page views on route change
        enableCorsCorrelation: true,   // Correlate with backend requests
        enableRequestHeaderTracking: true,
        enableResponseHeaderTracking: true,
        enableAjaxPerfTracking: true,  // Track AJAX performance
        maxAjaxCallsPerView: 100,
        disableFetchTracking: false,   // Track fetch requests
        autoTrackPageVisitTime: true,  // Track time on page
        enableUnhandledPromiseRejectionTracking: true,
      }
    });

    appInsights.loadAppInsights();
    appInsights.trackPageView(); // Track initial page view
    
    console.log('Application Insights initialized');
    return appInsights;
  } catch (error) {
    console.error('Failed to initialize Application Insights:', error);
    return null;
  }
}

/**
 * Get the Application Insights instance.
 */
export function getAppInsights(): ApplicationInsights | null {
  return appInsights;
}

/**
 * Get the React plugin for use with AppInsightsContext.
 */
export function getReactPlugin(): ReactPlugin {
  return reactPlugin;
}

/**
 * Track a custom event.
 * 
 * @example
 * trackEvent('DocumentOpened', { documentId: '123', source: 'search' });
 */
export function trackEvent(
  name: string, 
  properties?: Record<string, string | number | boolean>
): void {
  if (appInsights) {
    appInsights.trackEvent({ name }, properties);
  }
}

/**
 * Track an exception/error.
 * 
 * @example
 * trackException(new Error('API call failed'), { endpoint: '/api/documents' });
 */
export function trackException(
  error: Error, 
  properties?: Record<string, string>
): void {
  if (appInsights) {
    appInsights.trackException({ exception: error }, properties);
  }
}

/**
 * Track a page view manually (automatic tracking is enabled by default).
 */
export function trackPageView(name?: string, uri?: string): void {
  if (appInsights) {
    appInsights.trackPageView({ name, uri });
  }
}

/**
 * Track a metric value.
 * 
 * @example
 * trackMetric('SearchResultCount', 42);
 */
export function trackMetric(name: string, value: number): void {
  if (appInsights) {
    appInsights.trackMetric({ name, average: value });
  }
}

/**
 * Set authenticated user context.
 * Call this after user logs in.
 * 
 * @example
 * setAuthenticatedUser('user@example.com', 'user-123');
 */
export function setAuthenticatedUser(
  authenticatedUserId: string, 
  accountId?: string
): void {
  if (appInsights) {
    appInsights.setAuthenticatedUserContext(authenticatedUserId, accountId, true);
  }
}

/**
 * Clear user context on logout.
 */
export function clearAuthenticatedUser(): void {
  if (appInsights) {
    appInsights.clearAuthenticatedUserContext();
  }
}

