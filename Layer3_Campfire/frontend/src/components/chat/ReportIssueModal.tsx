'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Modal, Button, Dropdown } from '@/components/ui'
import type { DropdownOption } from '@/components/ui'
import { useSessionStore } from '@/stores/sessionStore'
import { trackReportIssueSubmitted } from '@/lib/telemetry'
import styles from './ReportIssueModal.module.css'

const CATEGORY_OPTIONS: readonly DropdownOption[] = [
  { label: 'Bug Report', value: 'bug' },
  { label: 'Inaccurate Response', value: 'inaccurate' },
  { label: 'Inappropriate Content', value: 'inappropriate' },
  { label: 'Other', value: 'other' },
] as const

interface ReportIssueModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ReportIssueModal({ open, onOpenChange }: ReportIssueModalProps) {
  const sessionId = useSessionStore((state) => state.sessionId)
  const [category, setCategory] = useState('')
  const [description, setDescription] = useState('')

  const resetForm = () => {
    setCategory('')
    setDescription('')
  }

  const handleSubmit = () => {
    trackReportIssueSubmitted({
      sessionId: sessionId ?? undefined,
      category: category || 'unspecified',
      description: description || undefined,
    })
    onOpenChange(false)
    resetForm()
    toast.success('Report submitted', {
      description: 'Thank you for your feedback.',
      style: { background: 'var(--color-night-sky)', color: 'var(--color-surface)', border: 'none' },
    })
  }

  const handleOpenChange = (nextOpen: boolean) => {
    onOpenChange(nextOpen)
    if (!nextOpen) resetForm()
  }

  return (
    <Modal open={open} onOpenChange={handleOpenChange} className={styles.modal}>
      <div className={styles.form}>
        <div className={styles.header}>
          <h2 className={styles.headline}>Report an Issue</h2>
          <p className={styles.description}>
            Help us improve by reporting any issues you encounter with this response.
          </p>
        </div>

        <Dropdown options={CATEGORY_OPTIONS} value={category} onChange={setCategory} placeholder="Issue Category" className={styles.dropdown} />

        <textarea
            className={styles.textarea}
            placeholder="Tell us about the issue"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />

        <div className={styles.submitButton}>
          <Button variant="primary" onClick={handleSubmit}>
            Submit Issue
          </Button>
        </div>
      </div>
    </Modal>
  )
}
