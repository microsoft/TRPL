// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

import { Accordion as BaseAccordion } from '@base-ui/react/accordion'
import { cn } from '@/lib/utils'
import styles from './Accordion.module.css'

interface AccordionRootProps {
  children: React.ReactNode
  className?: string
  defaultValue?: string[]
  value?: string[]
  onValueChange?: (value: string[]) => void
  disabled?: boolean
  multiple?: boolean
}

interface AccordionItemProps {
  children: React.ReactNode
  value: string
  className?: string
  disabled?: boolean
}

interface AccordionHeaderProps {
  children: React.ReactNode
  className?: string
}

interface AccordionTriggerProps {
  children: React.ReactNode
  className?: string
}

interface AccordionPanelProps {
  children: React.ReactNode
  className?: string
  keepMounted?: boolean
}

/**
 * Accordion Root - Groups all accordion parts
 */
function Root({
  children,
  className,
  defaultValue,
  value,
  onValueChange,
  disabled,
  multiple,
}: AccordionRootProps) {
  return (
    <BaseAccordion.Root
      className={cn(styles.root, className)}
      defaultValue={defaultValue}
      value={value}
      onValueChange={onValueChange}
      disabled={disabled}
      multiple={multiple}
    >
      {children}
    </BaseAccordion.Root>
  )
}

/**
 * Accordion Item - Groups header with panel
 */
function Item({ children, value, className, disabled }: AccordionItemProps) {
  return (
    <BaseAccordion.Item
      className={cn(styles.item, className)}
      value={value}
      disabled={disabled}
    >
      {children}
    </BaseAccordion.Item>
  )
}

/**
 * Accordion Header - Contains the trigger
 */
function Header({ children, className }: AccordionHeaderProps) {
  return (
    <BaseAccordion.Header render={<h2 />} className={cn(styles.header, className)}>
      {children}
    </BaseAccordion.Header>
  )
}

/**
 * Accordion Trigger - Button that opens/closes panel
 */
function Trigger({ children, className }: AccordionTriggerProps) {
  return (
    <BaseAccordion.Trigger className={cn(styles.trigger, className)}>
      {children}
    </BaseAccordion.Trigger>
  )
}

/**
 * Accordion Panel - Collapsible content area
 */
function Panel({ children, className, keepMounted = true }: AccordionPanelProps) {
  return (
    <BaseAccordion.Panel className={cn(styles.panel, className)} keepMounted={keepMounted}>
      {children}
    </BaseAccordion.Panel>
  )
}

export const Accordion = {
  Root,
  Item,
  Header,
  Trigger,
  Panel,
}
