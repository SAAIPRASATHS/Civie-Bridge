import type { HTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** A neutral placeholder block used while a panel's data loads. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('animate-pulse rounded-md bg-[var(--secondary)]', className)}
      {...props}
    />
  )
}

/** A thin horizontal rule. */
export function Separator({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div role="separator" className={cn('h-px w-full bg-[var(--border)]', className)} {...props} />
}

/** A 0..1 value rendered as a slim bar, used for confidence and relevance. */
export function Progress({
  value,
  tone = 'primary',
  className,
  label,
}: {
  value: number
  tone?: 'primary' | 'success' | 'warning' | 'danger' | 'info'
  className?: string
  label?: string
}) {
  const clamped = Math.max(0, Math.min(1, value))
  const fills = {
    primary: 'bg-[var(--primary)]',
    success: 'bg-[var(--success)]',
    warning: 'bg-[var(--warning)]',
    danger: 'bg-[var(--destructive)]',
    info: 'bg-[var(--info)]',
  } as const
  return (
    <div
      className={cn('h-2 w-full overflow-hidden rounded-full bg-[var(--secondary)]', className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
      aria-label={label}
    >
      <div
        className={cn('h-full rounded-full transition-[width]', fills[tone])}
        style={{ width: `${clamped * 100}%` }}
      />
    </div>
  )
}
