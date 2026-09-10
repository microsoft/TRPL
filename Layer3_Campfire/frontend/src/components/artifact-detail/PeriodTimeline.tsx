'use client'

import { useState } from 'react'
import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip'
import { PERIODS, resolvePeriodIndex } from '@/lib/periods'
import { Tooltip } from '@/components/ui'
import styles from './PeriodTimeline.module.css'

interface PeriodTimelineProps {
  period: string | undefined | null
}

const CURRENT_YEAR = new Date().getFullYear()
const MIN_DURATION = 1
const MAX_DURATION = 5

/**
 * Clamped duration for each period so the timeline is proportional
 * but outliers (Ancestry, Public Memory) don't crush everything else.
 */
const SEGMENT_WEIGHTS = PERIODS.map((p) => {
  const raw = (p.endYear ?? CURRENT_YEAR) - p.startYear
  return Math.max(MIN_DURATION, Math.min(raw, MAX_DURATION))
})

const TOTAL_WEIGHT = SEGMENT_WEIGHTS.reduce((sum, w) => sum + w, 0)

/** Cumulative percentage position for each dot (0 … 100). */
const DOT_POSITIONS: number[] = (() => {
  const positions: number[] = []
  let cumulative = 0
  for (let i = 0; i < SEGMENT_WEIGHTS.length; i++) {
    positions.push((cumulative / TOTAL_WEIGHT) * 100)
    cumulative += SEGMENT_WEIGHTS[i]
  }
  positions.push(100)
  return positions
})()

export function PeriodTimeline({ period }: PeriodTimelineProps) {
  const activeIndex = resolvePeriodIndex(period)
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)

  return (
    <BaseTooltip.Provider delay={0} closeDelay={0}>
      <div
        className={styles.timeline}
        role="img"
        aria-label={`Timeline showing ${PERIODS[activeIndex]?.label ?? 'unknown'} period`}
      >
        <div className={styles.track} />
        {activeIndex >= 0 && (
          <div
            className={styles.activeTrack}
            style={{
              left: `${DOT_POSITIONS[activeIndex]}%`,
              width: `${DOT_POSITIONS[activeIndex + 1] - DOT_POSITIONS[activeIndex]}%`,
            }}
          />
        )}
        {hoveredIndex !== null && (
          <div
            className={styles.hoverTrack}
            style={{
              left: `${DOT_POSITIONS[hoveredIndex]}%`,
              width: `${DOT_POSITIONS[hoveredIndex + 1] - DOT_POSITIONS[hoveredIndex]}%`,
            }}
          />
        )}
        {/* Hover regions for each segment — one per period */}
        {PERIODS.map((p, i) => {
          const tooltip = p.id === 'ancestry'
            ? p.label
            : p.id === 'public-memory'
              ? `${p.label} (${p.startYear}–Present)`
              : `${p.label} (${p.startYear}–${p.endYear})`
          return (
            <Tooltip key={p.id} label={tooltip}>
              <div
                className={styles.segment}
                style={{
                  left: `${DOT_POSITIONS[i]}%`,
                  width: `${DOT_POSITIONS[i + 1] - DOT_POSITIONS[i]}%`,
                }}
                onMouseEnter={() => setHoveredIndex(i)}
                onMouseLeave={() => setHoveredIndex(null)}
              />
            </Tooltip>
          )
        })}
        <div className={styles.dots}>
          {DOT_POSITIONS.map((pos, i) => {
            const isActive =
              activeIndex >= 0 && (i === activeIndex || i === activeIndex + 1)
            const isHovered =
              hoveredIndex !== null && (i === hoveredIndex || i === hoveredIndex + 1)
            return (
              <div
                key={i}
                className={`${styles.dot}${isActive ? ` ${styles.dotActive}` : ''}${isHovered ? ` ${styles.dotHovered}` : ''}`}
                style={{ left: `${pos}%` }}
              />
            )
          })}
        </div>
      </div>
    </BaseTooltip.Provider>
  )
}
