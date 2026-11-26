import type { HTMLAttributes } from 'react'

import { cn } from '@/lib/utils'
import type { Tone } from '@/lib/labels'

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'bg-[var(--secondary)] text-[var(--secondary-foreground)] ring-[var(--border)]',
  info: 'bg-[var(--info-subtle)] text-[var(--info)] ring-[var(--info-subtle)]',
  success: 'bg-[var(--success-subtle)] text-[var(--success)] ring-[var(--success-subtle)]',
  warning: 'bg-[var(--warning-subtle)] text-[var(--warning)] ring-[var(--warning-subtle)]',
  danger:
    'bg-[var(--destructive-subtle)] text-[var(--destructive)] ring-[var(--destructive-subtle)]',
  primary: 'bg-[var(--primary-subtle)] text-[var(--primary)] ring-[var(--primary-subtle)]',
}

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
}

/** A compact status pill. The tone carries the meaning; the label states it. */
export function Badge({ className, tone = 'neutral', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        TONE_CLASSES[tone],
        className,
      )}
      {...props}
    />
  )
}

/** A small coloured dot, for dense tables where a full pill would be noisy. */
export function ToneDot({ tone = 'neutral', className }: { tone?: Tone; className?: string }) {
  const dot: Record<Tone, string> = {
    neutral: 'bg-[var(--muted-foreground)]',
    info: 'bg-[var(--info)]',
    success: 'bg-[var(--success)]',
    warning: 'bg-[var(--warning)]',
    danger: 'bg-[var(--destructive)]',
    primary: 'bg-[var(--primary)]',
  }
  return <span className={cn('inline-block size-2 rounded-full', dot[tone], className)} />
}
