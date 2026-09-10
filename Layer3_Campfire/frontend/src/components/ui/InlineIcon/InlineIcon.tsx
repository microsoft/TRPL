import type { SVGProps } from 'react'

export type InlineIconName =
  | 'arrow-left'
  | 'arrow-right'
  | 'arrow-up'
  | 'chevron-down'
  | 'close'
  | 'copy'
  | 'info'
  | 'loading'
  | 'microphone'
  | 'minus'
  | 'panel'
  | 'plus'

interface InlineIconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: InlineIconName
  size?: number
}

const paths: Record<InlineIconName, string> = {
  'arrow-left': 'M14 4 6 12l8 8M6 12h12',
  'arrow-right': 'm10 4 8 8-8 8M18 12H6',
  'arrow-up': 'm5 12 7-7 7 7M12 5v14',
  'chevron-down': 'm6 9 6 6 6-6',
  close: 'M6 6l12 12M18 6 6 18',
  copy: 'M9 9h10v10H9zM5 15V5h10v4',
  info: 'M12 11v6M12 7h.01',
  loading: 'M20 12a8 8 0 1 1-8-8',
  microphone: 'M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0zM5 11v1a7 7 0 0 0 14 0v-1M12 19v3',
  minus: 'M5 12h14',
  panel: 'M4 5h16v14H4zM9 5v14',
  plus: 'M12 5v14M5 12h14',
}

export function InlineIcon({
  name,
  size = 16,
  className,
  ...props
}: InlineIconProps) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      data-icon={name}
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...props}
    >
      <path
        d={paths[name]}
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  )
}
