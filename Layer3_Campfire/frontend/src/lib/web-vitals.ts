// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Core Web Vitals + Gallery Image Performance Telemetry
 *
 * Captures real-user metrics and sends them as custom events to Application Insights
 * via the existing telemetry singleton. All functions are no-ops when telemetry is disabled.
 *
 * Metrics captured:
 *   - LCP, CLS, INP, FCP, TTFB (via web-vitals library)
 *   - Gallery image server-side optimization timing (via Resource Timing API)
 */

import { trackEvent, isTelemetryEnabled } from '@/lib/telemetry'

/**
 * Initialize Core Web Vitals tracking.
 * Dynamically imports web-vitals to keep the main bundle small.
 * Each metric fires a single 'WebVital' custom event in App Insights.
 */
export async function initWebVitals(): Promise<void> {
  if (!isTelemetryEnabled()) return

  try {
    const { onLCP, onCLS, onINP, onFCP, onTTFB } = await import('web-vitals')

    const sendMetric = (metric: { name: string; value: number; rating: string; id: string }) => {
      trackEvent('WebVital', {
        metricName: metric.name,
        metricValue: Math.round(metric.name === 'CLS' ? metric.value * 1000 : metric.value),
        rating: metric.rating,
        metricId: metric.id,
        navigationType:
          typeof performance !== 'undefined'
            ? (performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming)?.type ?? 'unknown'
            : 'unknown',
      })
    }

    onLCP(sendMetric)
    onCLS(sendMetric)
    onINP(sendMetric)
    onFCP(sendMetric)
    onTTFB(sendMetric)
  } catch (error) {
    console.warn('[web-vitals] Failed to initialize:', error)
  }
}

/**
 * Capture gallery image optimization performance via Resource Timing API.
 *
 * Looks for `_next/image` requests and reports aggregated stats.
 *
 * Should be called ~3s after page load to let the majority of images settle.
 */
export function trackGalleryImagePerformance(): void {
  if (!isTelemetryEnabled()) return
  if (typeof performance === 'undefined' || !performance.getEntriesByType) return

  const entries = performance.getEntriesByType('resource') as PerformanceResourceTiming[]

  // Next.js optimized images go through /_next/image?url=...
  const nextImageEntries = entries.filter((e) => e.name.includes('/_next/image'))
  if (nextImageEntries.length > 0) {
    const durations = nextImageEntries.map((e) => e.responseEnd - e.requestStart)
    const transferSizes = nextImageEntries.map((e) => e.transferSize ?? 0)

    trackEvent('GalleryImagePerformance', {
      source: 'next-image',
      imageCount: nextImageEntries.length,
      avgDurationMs: Math.round(durations.reduce((a, b) => a + b, 0) / durations.length),
      maxDurationMs: Math.round(Math.max(...durations)),
      minDurationMs: Math.round(Math.min(...durations)),
      p95DurationMs: Math.round(percentile(durations, 0.95)),
      avgTransferBytes: Math.round(transferSizes.reduce((a, b) => a + b, 0) / transferSizes.length),
      totalTransferBytes: transferSizes.reduce((a, b) => a + b, 0),
    })
  }

  // Also report if zero gallery-related resources found (useful for debugging)
  if (nextImageEntries.length === 0) {
    trackEvent('GalleryImagePerformance', {
      source: 'none',
      imageCount: 0,
      note: 'No gallery image resource timing entries found',
    })
  }
}

/**
 * Simple percentile calculation (sorted ascending).
 */
function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const index = Math.ceil(p * sorted.length) - 1
  return sorted[Math.max(0, index)]
}
