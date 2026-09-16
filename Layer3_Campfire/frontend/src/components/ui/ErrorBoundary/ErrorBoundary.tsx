// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { Component, type ReactNode, type ErrorInfo } from 'react'
import { trackException } from '@/lib/telemetry'
import styles from './ErrorBoundary.module.css'

interface ErrorBoundaryProps {
  children: ReactNode
  /** Optional fallback component to render on error */
  fallback?: ReactNode
  /** Optional callback when an error is caught */
  onError?: (error: Error, errorInfo: ErrorInfo) => void
  /** Optional reset key - when this changes, the error boundary resets */
  resetKey?: string | number
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

/**
 * ErrorBoundary - Catches JavaScript errors in child components
 * Displays a fallback UI instead of crashing the entire app
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Log to console in development
    if (process.env.NODE_ENV === 'development') {
      console.error('ErrorBoundary caught an error:', error, errorInfo)
    }

    // Report to Application Insights
    trackException(error, {
      source: 'ErrorBoundary',
      componentStack: errorInfo.componentStack || undefined,
    })

    // Call optional error handler
    this.props.onError?.(error, errorInfo)
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    // Reset error state when resetKey changes
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, error: null })
    }
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null })
  }

  render(): ReactNode {
    if (this.state.hasError) {
      // Use custom fallback if provided
      if (this.props.fallback) {
        return this.props.fallback
      }

      // Default fallback UI
      return (
        <div className={styles.container} role="alert">
          <h2 className={styles.title}>Something went wrong</h2>
          <p className={styles.message}>
            We encountered an unexpected error. Please try again.
          </p>
          {process.env.NODE_ENV === 'development' && this.state.error && (
            <pre className={styles.errorDetails}>
              {this.state.error.message}
            </pre>
          )}
          <button
            type="button"
            className={styles.resetButton}
            onClick={this.handleReset}
          >
            Try Again
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
