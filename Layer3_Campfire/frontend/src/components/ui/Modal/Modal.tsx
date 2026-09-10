'use client'

import { Dialog } from '@base-ui/react/dialog'
import { cn } from '@/lib/utils'
import styles from './Modal.module.css'

interface ModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title?: string
  children: React.ReactNode
  className?: string
}

/**
 * Modal/Dialog component
 * Wraps Base UI Dialog with custom styling
 */
export const Modal = ({
  open,
  onOpenChange,
  title,
  children,
  className,
}: ModalProps) => {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className={styles.backdrop} />
        <Dialog.Popup className={cn(styles.popup, className)}>
          {title && (
            <Dialog.Title className={styles.title}>{title}</Dialog.Title>
          )}
          {/* render={<div />} allows block-level children; default <p> would be invalid HTML */}
          <Dialog.Description render={<div />} className={styles.content}>
            {children}
          </Dialog.Description>
          <Dialog.Close className={styles.close} aria-label="Close">
            ×
          </Dialog.Close>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

