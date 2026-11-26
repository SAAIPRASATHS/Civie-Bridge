import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * A label/value list.
 *
 * Several fields on the officer panels are genuinely optional - a category may
 * be undetermined, an SLA may not apply - so `DescriptionItem` renders a
 * consistent em dash rather than hiding the row, which would make the absence
 * of a value indistinguishable from a missing field.
 */
export function DescriptionList({
  children,
  className,
  columns = 2,
}: {
  children: ReactNode
  className?: string
  columns?: 1 | 2 | 3
}) {
  const grid = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 sm:grid-cols-2',
    3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  } as const
  return <dl className={cn('grid gap-x-6 gap-y-4', grid[columns], className)}>{children}</dl>
}

export function DescriptionItem({
  label,
  children,
  className,
  full,
}: {
  label: string
  children: ReactNode
  className?: string
  full?: boolean
}) {
  return (
    <div className={cn('min-w-0 space-y-1', full && 'sm:col-span-2 lg:col-span-3', className)}>
      <dt className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
        {label}
      </dt>
      <dd className="break-words text-sm text-[var(--foreground)]">{children}</dd>
    </div>
  )
}

/** A value that may be absent, rendered as an em dash when it is. */
export function Value({ children }: { children: ReactNode }) {
  if (children === null || children === undefined || children === '') {
    return <span className="text-[var(--muted-foreground)]">-</span>
  }
  return <>{children}</>
}
