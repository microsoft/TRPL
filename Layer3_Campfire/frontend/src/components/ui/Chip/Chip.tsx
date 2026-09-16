// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { cn } from '@/lib/utils'
import styles from './Chip.module.css'

interface ChipProps {
  label: string
  variant?: 'default' | 'dark' | 'muted'
  selected?: boolean
  onClick?: () => void
  className?: string
  children?: React.ReactNode
  tabIndex?: number
}

/**
 * Chip/Tag component for topic selection and prompts
 *
 * Variants:
 * - default: Light sand background (for topic/action chips)
 * - dark: Dark forest green background (for suggested prompts)
 */
export const Chip = ({
  label,
  variant = 'default',
  selected = false,
  onClick,
  className,
  children,
  tabIndex = 0,
}: ChipProps) => {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={cn(
        styles.chip,
        variant === 'dark' && styles.dark,
        variant === 'muted' && styles.muted,
        selected && styles.selected,
        className
      )}
      onClick={onClick}
      tabIndex={tabIndex}
    >
      {children || label}
    </button>
  )
}

