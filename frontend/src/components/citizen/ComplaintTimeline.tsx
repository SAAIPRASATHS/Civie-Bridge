/**
 * The official status history, as a vertical timeline.
 *
 * These events are read from `complaint_status_history` - the record of what
 * actually happened to the complaint - so they are presented as fact, in
 * contrast to the AI panels, which are presented as suggestions.
 */

import { ToneDot } from '@/components/ui/badge'
import { EmptyState } from '@/components/common/states'
import { formatDateTime } from '@/lib/format'
import { statusLabel, statusTone } from '@/lib/labels'
import type { ComplaintStatus } from '@/types'

interface TimelineEvent {
  from_status: ComplaintStatus | null
  to_status: ComplaintStatus
  changed_at: string | null
  reason: string | null
  by_agent?: string | null
  changed_by_agent?: string | null
}

export function ComplaintTimeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return (
      <EmptyState
        title="No status events recorded yet"
        description="The complaint has been recorded but no status change has been logged."
      />
    )
  }

  return (
    <ol className="space-y-0">
      {events.map((event, index) => (
        <li key={`${event.to_status}-${event.changed_at ?? index}`} className="flex gap-3">
          <div className="flex flex-col items-center">
            <ToneDot tone={statusTone(event.to_status)} className="mt-1.5 size-2.5" />
            {index < events.length - 1 ? (
              <span className="my-1 w-px flex-1 bg-[var(--border)]" aria-hidden />
            ) : null}
          </div>
          <div className="min-w-0 flex-1 pb-5">
            <p className="text-sm font-medium text-[var(--foreground)]">
              {event.from_status
                ? `${statusLabel(event.from_status)} to ${statusLabel(event.to_status)}`
                : statusLabel(event.to_status)}
            </p>
            <p className="text-xs text-[var(--muted-foreground)]">
              {formatDateTime(event.changed_at)}
              {event.by_agent || event.changed_by_agent
                ? ` · recorded by ${(event.by_agent ?? event.changed_by_agent)?.replace(/_/g, ' ')}`
                : ''}
            </p>
            {event.reason ? (
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">{event.reason}</p>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  )
}
