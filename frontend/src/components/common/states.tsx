/** Loading, error and empty placeholders, so every panel has all three states. */

import { AlertTriangle, Inbox, Loader2, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/api'
import { cn } from '@/lib/utils'

export function LoadingState({ label = 'Loading', className }: { label?: string; className?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn('flex items-center justify-center gap-3 py-14 text-sm text-[var(--muted-foreground)]', className)}
    >
      <Loader2 className="size-4 animate-spin" aria-hidden />
      <span>{label}…</span>
    </div>
  )
}

/** Skeleton rows shaped like the table they stand in for. */
export function TableSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-2 p-4', className)} aria-hidden>
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  )
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
}: {
  title: string
  description?: string
  icon?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center gap-3 px-6 py-14 text-center', className)}>
      <span className="flex size-11 items-center justify-center rounded-full bg-[var(--secondary)] text-[var(--muted-foreground)]">
        {icon ?? <Inbox className="size-5" />}
      </span>
      <div className="space-y-1">
        <p className="font-medium text-[var(--foreground)]">{title}</p>
        {description ? (
          <p className="mx-auto max-w-md text-sm text-[var(--muted-foreground)]">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  )
}

/**
 * An error panel.
 *
 * When the failure is an `ApiError` its own user-facing message is shown, so a
 * 403 reads as a permission problem and a network failure as a connectivity
 * one, rather than a generic "something went wrong".
 */
export function ErrorState({
  error,
  onRetry,
  className,
  title,
}: {
  error: unknown
  onRetry?: () => void
  className?: string
  title?: string
}) {
  const message =
    error instanceof ApiError
      ? error.userMessage
      : error instanceof Error
        ? error.message
        : 'Something went wrong.'

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center gap-3 rounded-lg border border-[#f2c9c9] bg-[var(--destructive-subtle)] px-6 py-10 text-center',
        className,
      )}
    >
      <AlertTriangle className="size-6 text-[var(--destructive)]" aria-hidden />
      <div className="space-y-1">
        <p className="font-medium text-[#7d1a1a]">{title ?? 'Could not load this information'}</p>
        <p className="mx-auto max-w-md text-sm text-[#7d1a1a]/90">{message}</p>
        {error instanceof ApiError && error.fieldErrors && Object.keys(error.fieldErrors).length > 0 ? (
          <ul className="mx-auto mt-2 max-w-md space-y-0.5 text-left text-xs text-[#7d1a1a]/90">
            {Object.entries(error.fieldErrors).map(([field, detail]) => (
              <li key={field}>
                <span className="font-medium">{field}</span>: {detail}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Try again
        </Button>
      ) : null}
    </div>
  )
}
