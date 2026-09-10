'use client'

import * as React from 'react'
import { Menu } from '@base-ui/react/menu'
import { InlineIcon } from '@/components/ui/InlineIcon'
import styles from './Dropdown.module.css'

export interface DropdownOption {
  label: string
  value: string
  icon?: string
  description?: string
}

export type DropdownAnimation = 'fade' | 'slide'

interface DropdownProps {
  options: readonly DropdownOption[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  tabIndex?: number
  animation?: DropdownAnimation
  disabled?: boolean
}

/**
 * Dropdown - A styled dropdown menu using Base UI Menu
 */
export function Dropdown({ options, value, onChange, placeholder = 'Select...', className, tabIndex, animation = 'fade', disabled = false }: DropdownProps) {
  const selectedOption = options.find((opt) => opt.value === value)
  const animationClass = animation === 'slide' ? styles.animationSlide : styles.animationFade
  const triggerAnimationClass = animation === 'slide' ? styles.triggerSlide : styles.triggerFade

  return (
    <Menu.Root modal={false} disabled={disabled}>
      <Menu.Trigger className={`${styles.trigger} ${triggerAnimationClass} ${disabled ? styles.triggerDisabled : ''} ${className || ''}`} tabIndex={tabIndex} disabled={disabled}>
        {selectedOption?.label || placeholder}
        <InlineIcon name="chevron-down" size={12} className={styles.chevron} />
      </Menu.Trigger>
      <Menu.Portal keepMounted={animation === 'slide'}>
        <Menu.Positioner className={styles.positioner} sideOffset={10} align="start">
          <Menu.Popup className={`${styles.popup} ${animationClass}`}>
            {options.map((option) => (
              <Menu.Item
                key={option.value}
                className={`${styles.item} ${option.value === value ? styles.itemSelected : ''}`}
                onClick={() => onChange(option.value)}
              >
                {option.icon && (
                  <div className={styles.itemIconWrapper}>
                    <span className={styles.itemIcon} aria-hidden="true">
                      {option.icon}
                    </span>
                  </div>
                )}
                <div className={styles.itemContent}>
                  <span className={styles.itemLabel}>{option.label}</span>
                  {option.description && (
                    <span className={styles.itemDescription}>{option.description}</span>
                  )}
                </div>
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  )
}
