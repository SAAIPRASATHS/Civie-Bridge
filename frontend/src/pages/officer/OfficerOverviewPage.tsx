/**
 * The officer overview.
 *
 * Two panels answer the two questions an officer asks on arrival: how much work
 * is there, and what should I look at next. Every figure comes from the
 * server's own aggregation endpoint, and the queue preview is the real review
 * queue rather than a client-side slice of a different list.
 */

import { AlertTriangle, ClipboardCheck, Inbox, Layers, UserX } from 'lucide-react'
import { Link } from 'react-router-dom'

import { officerApi } from '@/api'
import { ComplaintsTrendChart } from '@/components/charts/AnalyticsCharts'
import { PageHeader } from '@/components/common/PageHeader'
import { StatCard } from '@/components/common/StatCard'
import { CategoryBadge, PriorityBadge, StatusBadge } from '@/components/common/badges'
import { EmptyState, ErrorState, LoadingState } from '@/components/common/states'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'
import { formatRelative, truncate } from '@/lib/format'

export default function OfficerOverviewPage() {
  useDocumentTitle('Officer overview')

  const analytics = useAsyncData(() => officerApi.analytics(30), [])
  const queue = useAsyncData(() => officerApi.reviewQueue(6, 0), [])

  const data = analytics.data

  return (
    <>
      <PageHeader
        eyebrow="Officer dashboard"
        title="Overview"
        description="The stored complaints, what still needs a human decision, and how the workload is distributed."
        actions={
          <Button asChild>
            <Link to="/officer/queue">
              <Inbox className="size-4" /> Open review queue
            </Link>
          </Button>
        }
      />

      {analytics.isLoading ? (
        <LoadingState label="Loading workload figures" />
      ) : analytics.error ? (
        <ErrorState error={analytics.error} onRetry={analytics.reload} />
      ) : data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Complaints on record"
              value={data.total_complaints}
              hint="All stored complaints"
              icon={<Layers className="size-4" />}
            />
            <StatCard
              label="Awaiting a decision"
              value={data.pending_review}
              hint="Not yet approved or rejected by an officer"
              tone={data.pending_review > 0 ? 'warning' : 'success'}
              icon={<ClipboardCheck className="size-4" />}
            />
            <StatCard
              label="SLA breached"
              value={data.sla_breached}
              hint="Prototype SLA window exceeded"
              tone={data.sla_breached > 0 ? 'danger' : 'success'}
              icon={<AlertTriangle className="size-4" />}
            />
            <StatCard
              label="Unassigned"
              value={data.unassigned}
              hint="No approved department yet"
              tone={data.unassigned > 0 ? 'info' : 'neutral'}
              icon={<UserX className="size-4" />}
            />
          </div>

          <ComplaintsTrendChart
            submitted={data.submitted_trend}
            resolved={data.resolved_trend}
            windowDays={data.window_days}
          />

          <Alert tone="info" title="Prototype figures">
            These counts describe the stored prototype complaints. They are not verified government
            statistics.
          </Alert>
        </>
      ) : null}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="space-y-1">
              <CardTitle>Next to review</CardTitle>
              <CardDescription>
                The oldest items still waiting on a human decision, newest first.
              </CardDescription>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link to="/officer/queue">View all</Link>
            </Button>
          </div>
        </CardHeader>

        <CardContent>
          {queue.isLoading ? (
            <LoadingState label="Loading the review queue" />
          ) : queue.error ? (
            <ErrorState error={queue.error} onRetry={queue.reload} />
          ) : queue.data && queue.data.items.length > 0 ? (
            <ul className="divide-y divide-[var(--border)]">
              {queue.data.items.map((item) => (
                <li key={item.complaint_id}>
                  <Link
                    to={`/officer/complaints/${item.complaint_id}`}
                    className="flex flex-col gap-2 py-3 transition-colors hover:bg-[var(--primary-subtle)] sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-[var(--primary)]">{item.reference_id}</span>
                        <StatusBadge status={item.status} />
                        <PriorityBadge priority={item.priority} />
                        <CategoryBadge category={item.category} />
                        {item.pending_human_review ? (
                          <span className="text-xs text-[var(--warning)]">Needs a decision</span>
                        ) : null}
                      </div>
                      <p className="truncate text-sm text-[var(--muted-foreground)]">
                        {truncate(item.location_text ?? 'No location recorded', 90)}
                      </p>
                    </div>
                    <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
                      {formatRelative(item.reported_at ?? item.submitted_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="Nothing is waiting for review"
              description="Every stored complaint already carries an officer decision."
            />
          )}
        </CardContent>
      </Card>
    </>
  )
}
