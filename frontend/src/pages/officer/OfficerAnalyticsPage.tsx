/**
 * Officer analytics.
 *
 * Every figure is computed by the backend's analytics endpoint for the selected
 * window; nothing is aggregated in the browser and nothing is charted that the
 * server did not count. The page states that the numbers describe stored
 * prototype complaints, so a figure is never mistaken for official statistics.
 */

import { CheckCircle2, ClipboardCheck, Inbox, Layers, Timer, UserX } from 'lucide-react'
import { useState } from 'react'

import { officerApi } from '@/api'
import {
  CategoryDistributionChart,
  ComplaintsTrendChart,
  DepartmentWorkloadChart,
  StatusDistributionChart,
} from '@/components/charts/AnalyticsCharts'
import { PageHeader } from '@/components/common/PageHeader'
import { StatCard } from '@/components/common/StatCard'
import { EmptyState, ErrorState, LoadingState } from '@/components/common/states'
import { Alert } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label, Select } from '@/components/ui/field'
import { Progress } from '@/components/ui/skeleton'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'
import { formatDateTime } from '@/lib/format'
import type { AnalyticsBucket } from '@/types'

const WINDOWS = [
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 365, label: 'Last 12 months' },
]

export default function OfficerAnalyticsPage() {
  useDocumentTitle('Analytics')
  const [windowDays, setWindowDays] = useState(30)

  const { data, error, isLoading, reload } = useAsyncData(
    () => officerApi.analytics(windowDays),
    [windowDays],
  )

  return (
    <>
      <PageHeader
        eyebrow="Officer dashboard"
        title="Analytics"
        description="Complaint volume, categorisation and workload across the stored records."
        actions={
          <div className="space-y-1.5">
            <Label htmlFor="analytics-window" className="sr-only">
              Reporting window
            </Label>
            <Select
              id="analytics-window"
              value={String(windowDays)}
              onChange={(event) => setWindowDays(Number(event.target.value))}
            >
              {WINDOWS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>
        }
      />

      {isLoading ? (
        <LoadingState label="Loading analytics" />
      ) : error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <StatCard
              label="Complaints on record"
              value={data.total_complaints}
              hint={`In the last ${data.window_days} days`}
              icon={<Layers className="size-4" />}
            />
            <StatCard
              label="Awaiting triage"
              value={data.awaiting_triage}
              hint="Not yet reviewed at intake"
              tone={data.awaiting_triage > 0 ? 'info' : 'neutral'}
              icon={<Inbox className="size-4" />}
            />
            <StatCard
              label="Awaiting a decision"
              value={data.pending_review}
              hint="No officer decision recorded"
              tone={data.pending_review > 0 ? 'warning' : 'success'}
              icon={<ClipboardCheck className="size-4" />}
            />
            <StatCard
              label="Decided"
              value={data.decided}
              hint="Approved or rejected by an officer"
              tone="success"
              icon={<CheckCircle2 className="size-4" />}
            />
            <StatCard
              label="SLA breached"
              value={data.sla_breached}
              hint="Prototype SLA window exceeded"
              tone={data.sla_breached > 0 ? 'danger' : 'success'}
              icon={<Timer className="size-4" />}
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

          <div className="grid gap-5 lg:grid-cols-2">
            <CategoryDistributionChart buckets={data.by_category} />
            <StatusDistributionChart buckets={data.by_status} />
          </div>

          <DepartmentWorkloadChart buckets={data.by_department} />

          <div className="grid gap-5 lg:grid-cols-2">
            <BucketBreakdown
              title="By priority"
              description="How the review workload is banded."
              buckets={data.by_priority}
            />
            <BucketBreakdown
              title="By language"
              description="The language each complaint was submitted in."
              buckets={data.by_language}
            />
          </div>

          <Alert tone="info" title="Prototype figures" role="note">
            <p>
              These counts describe stored prototype complaints and are not verified government
              statistics.
            </p>
            <p className="mt-1 text-xs">
              Generated {formatDateTime(data.generated_at)}. {data.provenance}
            </p>
          </Alert>
        </>
      ) : null}
    </>
  )
}

/**
 * A compact proportional list.
 *
 * Used for distributions whose labels are short enough not to need a chart
 * axis, so the page does not turn a two-value split into a misleading pie.
 */
function BucketBreakdown({
  title,
  description,
  buckets,
}: {
  title: string
  description: string
  buckets: AnalyticsBucket[]
}) {
  const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {buckets.length === 0 ? (
          <EmptyState title="No data" description="Nothing is recorded for this window." />
        ) : (
          <ul className="space-y-4">
            {buckets.map((bucket) => (
              <li key={bucket.key} className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <span>{bucket.label}</span>
                  <span className="tabular-nums text-[var(--muted-foreground)]">
                    {bucket.count}
                    {total > 0 ? ` (${Math.round((bucket.count / total) * 100)}%)` : ''}
                  </span>
                </div>
                <Progress
                  value={total > 0 ? bucket.count / total : 0}
                  tone="primary"
                  label={`${bucket.label} share`}
                />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
