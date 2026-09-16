// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import type { Ref } from 'react'
import { cn } from '@/lib/utils'
import styles from './Input.module.css'

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
  ref?: Ref<HTMLInputElement>
}

/**
 * Input component with optional label and error state
 * React 19: ref is now a regular prop, no forwardRef needed
 */
export function Input({ label, error, className, id, ref, ...props }: InputProps) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-')

  return (
    <div className={styles.wrapper}>
      {label && (
        <label htmlFor={inputId} className={styles.label}>
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={inputId}
        className={cn(styles.input, error && styles.error, className)}
        aria-invalid={!!error}
        aria-describedby={error ? `${inputId}-error` : undefined}
        {...props}
      />
      {error && (
        <span id={`${inputId}-error`} className={styles.errorMessage} role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

