'use client'

import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip'
import styles from './Tooltip.module.css'

interface TooltipProps {
  label: string
  children: React.ReactElement
}

/**
 * Tooltip - A styled tooltip using Base UI Tooltip with viewport-aware positioning
 */
export function Tooltip({ label, children }: TooltipProps) {
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger render={children} />
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner sideOffset={8} className={styles.positioner}>
          <BaseTooltip.Popup className={styles.popup}>
            {label}
          </BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  )
}
