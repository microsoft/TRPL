'use client'

import * as React from 'react'
import { Toolbar as BaseToolbar } from '@base-ui/react/toolbar'
import { cn } from '@/lib/utils'
import styles from './Toolbar.module.css'

interface ToolbarRootProps extends React.ComponentPropsWithoutRef<typeof BaseToolbar.Root> {
  className?: string
}

/**
 * Toolbar - Groups buttons and controls with keyboard navigation
 * Provides arrow key navigation between items
 */
function ToolbarRoot({ className, children, ...props }: ToolbarRootProps) {
  return (
    <BaseToolbar.Root className={cn(styles.root, className)} {...props}>
      {children}
    </BaseToolbar.Root>
  )
}

interface ToolbarButtonProps extends React.ComponentPropsWithoutRef<typeof BaseToolbar.Button> {
  className?: string
}

function ToolbarButton({ className, children, ...props }: ToolbarButtonProps) {
  return (
    <BaseToolbar.Button className={cn(styles.button, className)} {...props}>
      {children}
    </BaseToolbar.Button>
  )
}

interface ToolbarSeparatorProps extends React.ComponentPropsWithoutRef<typeof BaseToolbar.Separator> {
  className?: string
}

function ToolbarSeparator({ className, ...props }: ToolbarSeparatorProps) {
  return <BaseToolbar.Separator className={cn(styles.separator, className)} {...props} />
}

interface ToolbarGroupProps extends React.ComponentPropsWithoutRef<typeof BaseToolbar.Group> {
  className?: string
}

function ToolbarGroup({ className, children, ...props }: ToolbarGroupProps) {
  return (
    <BaseToolbar.Group className={cn(styles.group, className)} {...props}>
      {children}
    </BaseToolbar.Group>
  )
}

export const Toolbar = {
  Root: ToolbarRoot,
  Button: ToolbarButton,
  Separator: ToolbarSeparator,
  Group: ToolbarGroup,
}
