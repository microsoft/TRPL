// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useCallback, useEffect, useRef, useState } from 'react'
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react'

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'] as const

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

function toIsoDateString(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

function parseIsoDate(s: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s.trim())
  if (!m) return null
  const y = Number(m[1])
  const mo = Number(m[2]) - 1
  const day = Number(m[3])
  const d = new Date(y, mo, day)
  if (d.getFullYear() !== y || d.getMonth() !== mo || d.getDate() !== day) return null
  return d
}

function monthMatrix(year: number, month: number): (number | null)[][] {
  const first = new Date(year, month, 1)
  const startPad = first.getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: (number | null)[] = []
  for (let i = 0; i < startPad; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)
  while (cells.length % 7 !== 0) cells.push(null)
  const rows: (number | null)[][] = []
  for (let i = 0; i < cells.length; i += 7) {
    rows.push(cells.slice(i, i + 7))
  }
  return rows
}

function formatDisplayLabel(iso: string): string {
  const d = parseIsoDate(iso)
  if (!d) return iso
  return d.toLocaleDateString(undefined, {
    weekday: 'short',
    year: 'numeric',
    month: 'short',
    day: 'numeric'
  })
}

interface DateCalendarFieldProps {
  id?: string
  label: string
  value: string
  onChange: (isoDate: string) => void
  placeholder?: string
  /** When true, the picker does not open. */
  disabled?: boolean
  /** Override label row classes (default matches form field height with other inputs). */
  labelClassName?: string
  /** When false, label is omitted (use an external `<label htmlFor={id}>`); `label` is used as `aria-label` on the trigger. */
  showLabel?: boolean
}

export default function DateCalendarField({
  id,
  label,
  value,
  onChange,
  placeholder = 'Select date…',
  disabled = false,
  labelClassName = 'block text-sm font-medium text-gray-700 mb-1',
  showLabel = true,
}: DateCalendarFieldProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const selected = value ? parseIsoDate(value) : null
  const initialView = selected ?? new Date()
  const [viewYear, setViewYear] = useState(initialView.getFullYear())
  const [viewMonth, setViewMonth] = useState(initialView.getMonth())

  useEffect(() => {
    if (!open) return
    const base = value ? parseIsoDate(value) : new Date()
    const d = base ?? new Date()
    setViewYear(d.getFullYear())
    setViewMonth(d.getMonth())
  }, [open, value])

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  useEffect(() => {
    if (!open) return
    const onDocMouseDown = (e: MouseEvent) => {
      const el = rootRef.current
      if (el && !el.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [open])

  const goPrevMonth = useCallback(() => {
    setViewMonth((m) => {
      if (m === 0) {
        setViewYear((y) => y - 1)
        return 11
      }
      return m - 1
    })
  }, [])

  const goNextMonth = useCallback(() => {
    setViewMonth((m) => {
      if (m === 11) {
        setViewYear((y) => y + 1)
        return 0
      }
      return m + 1
    })
  }, [])

  const pickDay = useCallback(
    (day: number) => {
      const d = new Date(viewYear, viewMonth, day)
      onChange(toIsoDateString(d))
      setOpen(false)
    },
    [onChange, viewMonth, viewYear]
  )

  const clear = useCallback(() => {
    onChange('')
    setOpen(false)
  }, [onChange])

  const matrix = monthMatrix(viewYear, viewMonth)
  const monthLabel = new Date(viewYear, viewMonth, 1).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric'
  })

  const isSelectedDay = (day: number) => {
    if (!selected) return false
    return (
      selected.getFullYear() === viewYear &&
      selected.getMonth() === viewMonth &&
      selected.getDate() === day
    )
  }

  const today = new Date()
  const isToday = (day: number) =>
    today.getFullYear() === viewYear &&
    today.getMonth() === viewMonth &&
    today.getDate() === day

  return (
    <div className="relative flex min-h-0 min-w-0 flex-col" ref={rootRef}>
      {showLabel ? (
        <label htmlFor={id} className={labelClassName}>
          {label}
        </label>
      ) : null}
      <button
        type="button"
        id={id}
        disabled={disabled}
        aria-label={showLabel ? undefined : label}
        title={disabled ? 'Date selection is disabled' : undefined}
        onClick={() => {
          if (disabled) return
          setOpen((o) => !o)
        }}
        className={[
          'h-10 w-full flex items-center justify-between gap-2 px-3 border rounded-lg text-left text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-500',
          disabled
            ? 'border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed'
            : 'border-gray-300 bg-white text-gray-900 hover:border-gray-400',
        ].join(' ')}
      >
        <span className={value ? 'text-gray-900' : 'text-gray-400'}>
          {value ? formatDisplayLabel(value) : placeholder}
        </span>
        <Calendar className={`w-4 h-4 shrink-0 ${disabled ? 'text-gray-300' : 'text-indigo-500'}`} aria-hidden />
      </button>

      {open && (
        <div
          className="absolute left-0 right-0 z-[60] mt-1 p-3 bg-white border border-gray-200 rounded-xl shadow-xl"
          role="dialog"
          aria-label={`Calendar: ${label}`}
        >
          <div className="flex items-center justify-between mb-2">
            <button
              type="button"
              onClick={goPrevMonth}
              className="p-1 rounded-lg text-gray-600 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              aria-label="Previous month"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <span className="text-sm font-semibold text-gray-900">{monthLabel}</span>
            <button
              type="button"
              onClick={goNextMonth}
              className="p-1 rounded-lg text-gray-600 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              aria-label="Next month"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-0.5 text-center text-xs font-medium text-gray-500 mb-1">
            {WEEKDAYS.map((d) => (
              <div key={d} className="py-1">
                {d}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-0.5">
            {matrix.flat().map((day, idx) =>
              day === null ? (
                <div key={`e-${idx}`} className="aspect-square" />
              ) : (
                <button
                  key={day}
                  type="button"
                  onClick={() => pickDay(day)}
                  className={[
                    'aspect-square rounded-lg text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-inset',
                    isSelectedDay(day)
                      ? 'bg-indigo-600 text-white'
                      : isToday(day)
                        ? 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100'
                        : 'text-gray-800 hover:bg-gray-100'
                  ].join(' ')}
                >
                  {day}
                </button>
              )
            )}
          </div>

          <div className="mt-2 pt-2 border-t border-gray-100 flex justify-end">
            <button
              type="button"
              onClick={clear}
              className="text-xs font-medium text-gray-500 hover:text-gray-800 px-2 py-1 rounded focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
