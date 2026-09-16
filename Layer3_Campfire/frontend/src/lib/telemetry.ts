// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Application Insights telemetry service
 * Client-side only - uses dynamic imports to avoid SSR issues
 * All functions are no-ops when telemetry is disabled
 */

import type { ApplicationInsights } from '@microsoft/applicationinsights-web'
import type { ReactPlugin } from '@microsoft/applicationinsights-react-js'

// Singleton instance
let appInsights: ApplicationInsights | null = null
let reactPlugin: ReactPlugin | null = null
let initPromise: Promise<{ appInsights: ApplicationInsights | null; reactPlugin: ReactPlugin | null }> | null = null

/**
 * Check if we're running in a browser environment
 */
const isBrowser = typeof window !== 'undefined'

/**
 * Check if telemetry should be enabled
 */
const shouldEnableTelemetry = (): boolean => {
  if (!isBrowser) return false
  const connectionString = process.env.NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING
  return !!connectionString
}

// Session tracking
let sessionStartTime: number | null = null

/**
 * Event interfaces for type-safe tracking
 */
export interface ChatMessageEvent {
  messageLength: number
  action?: string
  topic?: string
  sessionId?: string
}

export interface ChatResponseEvent {
  responseTimeMs: number
  contentLength: number
  hasAttachments: boolean
  attachmentCount?: number
  sessionId?: string
}

export interface ChatErrorEvent {
  errorType: 'agent_error' | 'connection_error' | 'unknown_error'
  sessionId?: string
  responseTimeMs?: number
}

export interface SessionStartEvent {
  sessionId: string
}

export interface SessionEndEvent {
  sessionId: string
  durationMs: number
  messageCount: number
}

export interface SearchQueryEvent {
  action?: string
  topic?: string
  composedQuery: string
  queryLength: number
}

export interface ApiErrorEvent {
  endpoint: string
  statusCode?: number
  errorMessage: string
}

export interface CitationOpenedEvent {
  sessionId?: string
  /** 1-based citation index as numbered in the response */
  sourceIndex?: number
  source?: 'letter' | 'book' | string
  resourceType?: string
}

export interface ResponseCopiedEvent {
  sessionId?: string
  responseLength: number
}

export interface StopResponseEvent {
  sessionId?: string
  /** Milliseconds since the request started */
  elapsedMs: number
}

export interface ReportIssueEvent {
  sessionId?: string
  /** Empty on Opened; populated on Submitted */
  category?: string
  /** Free-text description entered by the user */
  description?: string
}

/**
 * Initialize Application Insights
 * Should be called once on client-side mount
 * Uses a promise guard to prevent multiple concurrent initializations
 */
export function initializeTelemetry(): Promise<{
  appInsights: ApplicationInsights | null
  reactPlugin: ReactPlugin | null
}> {
  if (!shouldEnableTelemetry()) {
    return Promise.resolve({ appInsights: null, reactPlugin: null })
  }

  // Return existing promise if initialization is in progress or complete
  if (initPromise) {
    return initPromise
  }

  initPromise = (async () => {
    try {
      // Dynamic imports to avoid SSR bundling issues
      const [{ ApplicationInsights }, { ReactPlugin }] = await Promise.all([
        import('@microsoft/applicationinsights-web'),
        import('@microsoft/applicationinsights-react-js'),
      ])

      reactPlugin = new ReactPlugin()

      appInsights = new ApplicationInsights({
        config: {
          connectionString: process.env.NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING,
          extensions: [reactPlugin],
          enableAutoRouteTracking: true,
          disableAjaxTracking: false,
          autoTrackPageVisitTime: true,
          enableCorsCorrelation: true,
          enableRequestHeaderTracking: true,
          enableResponseHeaderTracking: true,
        },
      })

      appInsights.loadAppInsights()

      return { appInsights, reactPlugin }
    } catch (error) {
      console.warn('Failed to initialize Application Insights:', error)
      return { appInsights: null, reactPlugin: null }
    }
  })()

  return initPromise
}

/**
 * Get the React plugin instance (for AppInsightsContext)
 */
export function getReactPlugin(): ReactPlugin | null {
  return reactPlugin
}

/**
 * Track a custom event
 */
export function trackEvent(name: string, properties?: Record<string, string | number | boolean | undefined>): void {
  if (!appInsights) return

  // Filter out undefined values
  const cleanProperties: Record<string, string | number | boolean> = {}
  if (properties) {
    for (const [key, value] of Object.entries(properties)) {
      if (value !== undefined) {
        cleanProperties[key] = value
      }
    }
  }

  appInsights.trackEvent({ name }, cleanProperties)
}

/**
 * Track a chat message being sent
 */
export function trackChatMessage(event: ChatMessageEvent): void {
  trackEvent('ChatMessageSent', {
    messageLength: event.messageLength,
    action: event.action,
    topic: event.topic,
    sessionId: event.sessionId,
  })
}

/**
 * Track a chat response being received
 */
export function trackChatResponse(event: ChatResponseEvent): void {
  trackEvent('ChatResponseReceived', {
    responseTimeMs: event.responseTimeMs,
    contentLength: event.contentLength,
    hasAttachments: event.hasAttachments,
    attachmentCount: event.attachmentCount,
    sessionId: event.sessionId,
  })
}

/**
 * Track a search query
 */
export function trackSearchQuery(event: SearchQueryEvent): void {
  trackEvent('SearchQuery', {
    action: event.action,
    topic: event.topic,
    composedQuery: event.composedQuery,
    queryLength: event.queryLength,
  })
}

/**
 * Track an API error
 */
export function trackApiError(event: ApiErrorEvent): void {
  trackEvent('ApiError', {
    endpoint: event.endpoint,
    statusCode: event.statusCode,
    errorMessage: event.errorMessage,
  })
}

/**
 * Track a chat error (agent unable to respond or connection error)
 */
export function trackChatError(event: ChatErrorEvent): void {
  trackEvent('ChatError', {
    errorType: event.errorType,
    sessionId: event.sessionId,
    responseTimeMs: event.responseTimeMs,
  })
}

/**
 * Track session start
 */
export function trackSessionStart(event: SessionStartEvent): void {
  sessionStartTime = Date.now()
  trackEvent('SessionStart', {
    sessionId: event.sessionId,
  })
}

/**
 * Track session end with duration
 */
export function trackSessionEnd(event: SessionEndEvent): void {
  trackEvent('SessionEnd', {
    sessionId: event.sessionId,
    durationMs: event.durationMs,
    messageCount: event.messageCount,
  })
  sessionStartTime = null
}

/**
 * Get the session start time (for calculating duration)
 */
export function getSessionStartTime(): number | null {
  return sessionStartTime
}

/**
 * Track a user opening a specific citation (clicking through to the source).
 * Distinct from the View Sources toggle — fired per-source navigation.
 */
export function trackCitationOpened(event: CitationOpenedEvent): void {
  trackEvent('CitationOpened', {
    sessionId: event.sessionId,
    sourceIndex: event.sourceIndex,
    source: event.source,
    resourceType: event.resourceType,
  })
}

/**
 * Track a user copying an assistant response to the clipboard.
 */
export function trackResponseCopied(event: ResponseCopiedEvent): void {
  trackEvent('ResponseCopied', {
    sessionId: event.sessionId,
    responseLength: event.responseLength,
  })
}

/**
 * Track a user cancelling an in-progress streaming response.
 */
export function trackStopResponse(event: StopResponseEvent): void {
  trackEvent('StopResponse', {
    sessionId: event.sessionId,
    elapsedMs: event.elapsedMs,
  })
}

/**
 * Track Report Issue modal lifecycle. Pass no category on open;
 * include category on submit.
 */
export function trackReportIssueOpened(event: ReportIssueEvent): void {
  trackEvent('ReportIssueOpened', {
    sessionId: event.sessionId,
  })
}

export function trackReportIssueSubmitted(event: ReportIssueEvent): void {
  trackEvent('ReportIssueSubmitted', {
    sessionId: event.sessionId,
    category: event.category,
    description: event.description,
  })
}

/**
 * Track an exception/error
 */
export function trackException(
  error: Error,
  properties?: Record<string, string | number | boolean | undefined>
): void {
  if (!appInsights) return

  // Filter out undefined values
  const cleanProperties: Record<string, string | number | boolean> = {}
  if (properties) {
    for (const [key, value] of Object.entries(properties)) {
      if (value !== undefined) {
        cleanProperties[key] = value
      }
    }
  }

  appInsights.trackException({ exception: error }, cleanProperties)
}

/**
 * Flush telemetry data (useful before page unload)
 */
export function flushTelemetry(): void {
  if (!appInsights) return
  appInsights.flush()
}

/**
 * Check if telemetry is enabled and initialized
 */
export function isTelemetryEnabled(): boolean {
  return appInsights !== null
}
