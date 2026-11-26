import type { ReactNode } from 'react'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

/**
 * A single headline figure.
 *
 * `tone` colours the figure's accent, which is used sparingly: a breach count is
 * only red when there is a breach, so the dashboard's colour means something.
 */
export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = 'neutral',
  className,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  tone?: 'neutral' | 'primary' | 'warning' | 'danger' | 'success' | 'info'
  className?: string
}) {
  const accents = {
    neutral: 'text-[var(--foreground)]',
    primary: 'text-[var(--primary)]',
    warning: 'text-[var(--warning)]',
    danger: 'text-[var(--destructive)]',
    success: 'text-[var(--success)]',
    info: 'text-[var(--info)]',
  } as const

  return (
    <Card className={cn('p-5', className)}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
          {label}
        </p>
        {icon ? <span className="text-[var(--muted-foreground)]">{icon}</span> : null}
      </div>
      <p className={cn('mt-2 text-3xl font-semibold tabular-nums tracking-tight', accents[tone])}>
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-[var(--muted-foreground)]">{hint}</p> : null}
    </Card>
  )
}

/** A small up/down/flat delta used beside a trend figure. */
export function TrendDelta({ value }: { value: number }) {
  if (value === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
        <Minus className="size-3" /> no change
      </span>
    )
  }
  const rising = value > 0
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 text-xs',
        rising ? 'text-[var(--destructive)]' : 'text-[var(--success)]',
      )}
    >
      {rising ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}
      {rising ? '+' : ''}
      {value}
    </span>
  )
}
