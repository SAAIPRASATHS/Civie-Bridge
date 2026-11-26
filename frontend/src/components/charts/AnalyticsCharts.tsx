/**
 * Presentational analytics charts for the officer dashboard.
 *
 * These components only draw the data they are handed - no fetching, no
 * derived business meaning. Series are merged by `period` so a day present in
 * only one of the submitted/resolved series still renders (as a zero) rather
 * than silently dropping the row.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { EmptyState } from '@/components/common/states'
import { formatDay } from '@/lib/format'
import type { AnalyticsBucket, AnalyticsTrendPoint } from '@/types'

const CHART_HEIGHT = 280
const AXIS_FONT_SIZE = 12

/** A fixed, accessible palette for the pie slices, cycled if there are more. */
const PIE_COLORS = ['#1d4ed8', '#0f9d58', '#f59e0b', '#dc2626', '#7c3aed', '#0ea5e9', '#db2777', '#65a30d']

export function ComplaintsTrendChart({
  submitted,
  resolved,
  windowDays,
}: {
  submitted: AnalyticsTrendPoint[]
  resolved: AnalyticsTrendPoint[]
  windowDays: number
}) {
  const rows = mergeTrends(submitted, resolved)
  const isEmpty = rows.length === 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>Complaints submitted and resolved</CardTitle>
        <CardDescription>Daily counts over the last {windowDays} days.</CardDescription>
      </CardHeader>
      <CardContent>
        {isEmpty ? (
          <EmptyState
            title="No trend data"
            description="There is no submitted or resolved activity in this window."
          />
        ) : (
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <LineChart data={rows} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis
                dataKey="period"
                tickFormatter={(value: string) => formatDay(value)}
                fontSize={AXIS_FONT_SIZE}
              />
              <YAxis allowDecimals={false} fontSize={AXIS_FONT_SIZE} />
              <Tooltip labelFormatter={(value) => formatDay(String(value))} />
              <Legend />
              <Line
                type="monotone"
                dataKey="submitted"
                name="Submitted"
                stroke="var(--primary)"
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="resolved"
                name="Resolved"
                stroke="var(--success)"
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

export function CategoryDistributionChart({ buckets }: { buckets: AnalyticsBucket[] }) {
  const data = buckets.map((bucket) => ({ label: bucket.label, count: bucket.count }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Complaints by category</CardTitle>
        <CardDescription>How this window's complaints are classified.</CardDescription>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <EmptyState
            title="No category data"
            description="No complaints in this window carry a category."
          />
        ) : (
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <BarChart
              data={data}
              layout="vertical"
              margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
              <XAxis type="number" allowDecimals={false} fontSize={AXIS_FONT_SIZE} />
              <YAxis type="category" dataKey="label" width={140} fontSize={AXIS_FONT_SIZE} />
              <Tooltip />
              <Bar dataKey="count" name="Complaints" fill="var(--primary)" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

export function StatusDistributionChart({ buckets }: { buckets: AnalyticsBucket[] }) {
  const data = buckets.map((bucket) => ({ label: bucket.label, count: bucket.count }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Complaints by status</CardTitle>
        <CardDescription>The official recorded status of this window's complaints.</CardDescription>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <EmptyState
            title="No status data"
            description="No complaints are recorded in this window."
          />
        ) : (
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie data={data} dataKey="count" nameKey="label" outerRadius="80%">
                {data.map((entry, index) => (
                  <Cell key={entry.label} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

export function DepartmentWorkloadChart({ buckets }: { buckets: AnalyticsBucket[] }) {
  const data = buckets.map((bucket) => ({ label: bucket.label, count: bucket.count }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Complaints by department</CardTitle>
        <CardDescription>Workload across the departments appearing in this window.</CardDescription>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <EmptyState
            title="No department data"
            description="No complaints in this window are assigned to a department."
          />
        ) : (
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            {/* A horizontal layout keeps long department names readable. */}
            <BarChart
              data={data}
              layout="vertical"
              margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
              <XAxis type="number" allowDecimals={false} fontSize={AXIS_FONT_SIZE} />
              <YAxis type="category" dataKey="label" width={160} fontSize={AXIS_FONT_SIZE} />
              <Tooltip />
              <Bar dataKey="count" name="Complaints" fill="var(--primary)" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

/** Merge the two daily series into one row per period, defaulting absent days to 0. */
function mergeTrends(
  submitted: AnalyticsTrendPoint[],
  resolved: AnalyticsTrendPoint[],
): Array<{ period: string; submitted: number; resolved: number }> {
  const rows = new Map<string, { period: string; submitted: number; resolved: number }>()
  for (const point of submitted) {
    rows.set(point.period, { period: point.period, submitted: point.count, resolved: 0 })
  }
  for (const point of resolved) {
    const existing = rows.get(point.period)
    if (existing) existing.resolved = point.count
    else rows.set(point.period, { period: point.period, submitted: 0, resolved: point.count })
  }
  return Array.from(rows.values()).sort((a, b) => a.period.localeCompare(b.period))
}
