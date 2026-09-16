// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { Button as BaseButton } from '@base-ui/react/button'
import { cn } from '@/lib/utils'
import styles from './Button.module.css'

interface ButtonBaseProps {
  variant?: 'primary' | 'secondary' | 'ghost' | 'soft'
  size?: 'sm' | 'md' | 'lg'
  children: React.ReactNode
  className?: string
}

interface ButtonAsButton extends ButtonBaseProps, Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, keyof ButtonBaseProps> {
  href?: never
}

interface ButtonAsAnchor extends ButtonBaseProps, Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, keyof ButtonBaseProps> {
  href: string
}

type ButtonProps = ButtonAsButton | ButtonAsAnchor

/**
 * Button component
 * Wraps Base UI Button with custom styling
 * Renders as anchor when href is provided
 */
export const Button = ({
  variant = 'primary',
  size = 'md',
  className,
  children,
  ...props
}: ButtonProps) => {
  const classNames = cn(styles.button, styles[variant], styles[size], className)

  if ('href' in props && props.href) {
    const { href, ...anchorProps } = props as ButtonAsAnchor
    return (
      <a href={href} className={classNames} {...anchorProps}>
        {children}
      </a>
    )
  }

  return (
    <BaseButton
      className={classNames}
      {...(props as ButtonAsButton)}
    >
      {children}
    </BaseButton>
  )
}

