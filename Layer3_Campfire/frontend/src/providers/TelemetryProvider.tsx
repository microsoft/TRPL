'use client'

import { useEffect, useState, createContext, useContext, type ReactNode } from 'react'
import {
  initializeTelemetry,
  flushTelemetry,
  trackSessionEnd,
  getSessionStartTime,
} from '@/lib/telemetry'
import { initWebVitals, trackGalleryImagePerformance } from '@/lib/web-vitals'
import { useSessionStore } from '@/stores/sessionStore'

interface TelemetryContextValue {
  isEnabled: boolean
}

const TelemetryContext = createContext<TelemetryContextValue>({ isEnabled: false })

interface TelemetryProviderProps {
  children: ReactNode
}

/**
 * TelemetryProvider - Initializes Application Insights on the client
 * Wraps children with AppInsightsContext when telemetry is enabled
 */
export const TelemetryProvider = ({ children }: TelemetryProviderProps) => {
  const [isEnabled, setIsEnabled] = useState(false)
  const [AppInsightsContext, setAppInsightsContext] = useState<React.ComponentType<{
    children: ReactNode
  }> | null>(null)

  useEffect(() => {
    let mounted = true

    const init = async () => {
      const { reactPlugin } = await initializeTelemetry()

      if (!mounted) return

      if (reactPlugin) {
        // Dynamically import the context component
        const { AppInsightsContext: AIContext } = await import(
          '@microsoft/applicationinsights-react-js'
        )

        if (!mounted) return

        // Create a wrapper component that provides the react plugin
        const ContextWrapper = ({ children }: { children: ReactNode }) => (
          <AIContext.Provider value={reactPlugin}>{children}</AIContext.Provider>
        )

        setAppInsightsContext(() => ContextWrapper)
        setIsEnabled(true)

        // Fire-and-forget: Core Web Vitals + gallery image perf
        initWebVitals()
        setTimeout(() => trackGalleryImagePerformance(), 3000)
      }
    }

    init()

    // Track session end and flush telemetry on page unload
    const handleBeforeUnload = () => {
      const sessionStartTime = getSessionStartTime()
      const { sessionId, messages } = useSessionStore.getState()

      // Only track session end if a session was started
      if (sessionId && sessionStartTime) {
        trackSessionEnd({
          sessionId,
          durationMs: Date.now() - sessionStartTime,
          messageCount: messages.filter((m) => m.role === 'user').length,
        })
      }

      flushTelemetry()
    }
    window.addEventListener('beforeunload', handleBeforeUnload)

    return () => {
      mounted = false
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [])

  const contextValue: TelemetryContextValue = { isEnabled }

  // Wrap with AppInsightsContext if available
  const content = (
    <TelemetryContext.Provider value={contextValue}>
      {children}
    </TelemetryContext.Provider>
  )

  if (AppInsightsContext) {
    return <AppInsightsContext>{content}</AppInsightsContext>
  }

  return content
}

/**
 * Hook to check telemetry status
 */
export const useTelemetry = (): TelemetryContextValue => {
  return useContext(TelemetryContext)
}
