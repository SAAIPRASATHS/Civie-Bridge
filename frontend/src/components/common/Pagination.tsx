import { Button } from '@/components/ui/button'

/**
 * Offset/limit pagination for the complaint lists.
 *
 * The backend's page envelope reports `total`, `limit` and `offset` directly, so
 * the control is driven by the server's own numbers rather than a client guess.
 */
export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
}) {
  const first = total === 0 ? 0 : offset + 1
  const last = Math.min(offset + limit, total)
  const canGoBack = offset > 0
  const canGoForward = offset + limit < total

  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] px-4 py-3 text-sm"
    >
      <p className="text-[var(--muted-foreground)]">
        Showing <span className="font-medium text-[var(--foreground)]">{first}</span>-
        <span className="font-medium text-[var(--foreground)]">{last}</span> of{' '}
        <span className="font-medium text-[var(--foreground)]">{total}</span>
      </p>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!canGoBack}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!canGoForward}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </Button>
      </div>
    </nav>
  )
}
