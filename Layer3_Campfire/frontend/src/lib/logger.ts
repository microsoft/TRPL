/**
 * Simple logging utility for consistent error handling
 * Sends errors and warnings to Application Insights in production
 */

import { trackException, trackEvent } from '@/lib/telemetry'

type LogLevel = 'debug' | 'info' | 'warn' | 'error'

interface LogEntry {
  level: LogLevel
  message: string
  context?: Record<string, unknown>
  timestamp: string
}

/** Check at call time so tests can override NODE_ENV between imports */
const isDev = (): boolean => process.env.NODE_ENV === 'development'

/**
 * Format and output log entry
 */
const log = (level: LogLevel, message: string, context?: Record<string, unknown>): void => {
  const entry: LogEntry = {
    level,
    message,
    context,
    timestamp: new Date().toISOString(),
  }

  // In development, use console methods for better DevTools integration
  if (isDev()) {
    const consoleMethod = level === 'error' ? console.error : level === 'warn' ? console.warn : console.log
    consoleMethod(`[${level.toUpperCase()}] ${message}`, context || '')
    return
  }

  // In production, send to Application Insights and console
  if (level === 'error') {
    console.error(`[${entry.timestamp}] ${message}`, context || '')
    // Send to Application Insights
    const error = context?.error instanceof Error ? context.error : new Error(message)
    trackException(error, { source: 'logger', ...context as Record<string, string | number | boolean | undefined> })
  } else if (level === 'warn') {
    console.warn(`[${entry.timestamp}] ${message}`, context || '')
    // Send to Application Insights
    trackEvent('Warning', { message, ...context as Record<string, string | number | boolean | undefined> })
  }
}

export const logger = {
  debug: (message: string, context?: Record<string, unknown>) => log('debug', message, context),
  info: (message: string, context?: Record<string, unknown>) => log('info', message, context),
  warn: (message: string, context?: Record<string, unknown>) => log('warn', message, context),
  error: (message: string, context?: Record<string, unknown>) => log('error', message, context),
}
