/**
 * The citizen dashboard.
 *
 * It summarises the citizen's own complaints. The figures are derived from the
 * list endpoint rather than a separate stats call, so what the cards say is
 * always consistent with the table below them.
 */

import { CircleCheckBig, ClipboardList, Info, Plus, Send } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'

import { citizenApi } from '@/api'
import { CategoryBadge, PriorityBadge, StatusBadge } from '@/components/common/badges'
import { Value } from '@/components/common/DescriptionList'
import { PageHeader } from '@/components/common/PageHeader'
import { StatCard } from '@/components/common/StatCard'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/common/states'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  TableWrapper,
} from '@/components/ui/table'
import { formatDate } from '@/lib/format'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'
import type { ComplaintStatus } from '@/types'

/** Statuses where the citizen is no longer waiting on anybody. */
const SETTLED: ComplaintStatus[] = ['resolved', 'closed', 'rejected', 'duplicate']

function newestFirst<T extends { submitted_at: string | null }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => new Date(b.submitted_at ?? 0).getTime() - new Date(a.submitted_at ?? 0).getTime(),
  )
}

export default function CitizenDashboardPage() {
  useDocumentTitle('Dashboard')
  const navigate = useNavigate()

  const { data, error, isLoading, reload } = useAsyncData(() => citizenApi.listMine(100, 0), [])

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const awaiting = items.filter((item) => !SETTLED.includes(item.status)).length
  const resolved = items.filter((item) => item.status === 'resolved').length
  const clarifications = items.filter((item) => item.needs_clarification).length
  const recent = newestFirst(items).slice(0, 5)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Your complaints"
        description="Every complaint you submit is read by the AI triage agents, which suggest a category, a priority and a department. An officer reviews those suggestions and makes the official decision."
        actions={
          <Button asChild>
            <Link to="/app/submit">
              <Send className="size-4" /> Submit a complaint
            </Link>
          </Button>
        }
      />

      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Total complaints"
              value={isLoading ? '—' : total}
              hint="All complaints on your account"
              icon={<ClipboardList className="size-4" />}
            />
            <StatCard
              label="Awaiting officer action"
              value={isLoading ? '—' : awaiting}
              hint="Still open with the council"
              tone="warning"
              icon={<Send className="size-4" />}
            />
            <StatCard
              label="Resolved"
              value={isLoading ? '—' : resolved}
              hint="Marked resolved"
              tone="success"
              icon={<CircleCheckBig className="size-4" />}
            />
            <StatCard
              label="Needs clarification"
              value={isLoading ? '—' : clarifications}
              hint="An officer has asked a question"
              tone="info"
              icon={<Info className="size-4" />}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Recent complaints</CardTitle>
              <CardDescription>
                The five most recently submitted complaints on your account.
              </CardDescription>
            </CardHeader>
            <CardContent className="px-0 pb-0">
              {isLoading ? (
                <TableSkeleton rows={5} />
              ) : recent.length === 0 ? (
                <EmptyState
                  title="No complaints yet"
                  description="When you submit a complaint it will appear here with its reference number and status."
                  icon={<ClipboardList className="size-5" />}
                  action={
                    <Button asChild>
                      <Link to="/app/submit">
                        <Plus className="size-4" /> Submit a complaint
                      </Link>
                    </Button>
                  }
                />
              ) : (
                <TableWrapper className="rounded-none border-0">
                  <Table>
                    <TableHead>
                      <TableRow>
                        <TableHeaderCell>Reference</TableHeaderCell>
                        <TableHeaderCell>Status</TableHeaderCell>
                        <TableHeaderCell>Category</TableHeaderCell>
                        <TableHeaderCell>Priority</TableHeaderCell>
                        <TableHeaderCell>Submitted</TableHeaderCell>
                        <TableHeaderCell>Ward</TableHeaderCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {recent.map((item) => (
                        <TableRow
                          key={item.complaint_id}
                          interactive
                          onClick={() => navigate(`/app/complaints/${item.reference_id}`)}
                        >
                          <TableCell>
                            <Link
                              to={`/app/complaints/${item.reference_id}`}
                              className="font-medium text-[var(--primary)] underline-offset-4 hover:underline"
                              onClick={(event) => event.stopPropagation()}
                            >
                              {item.reference_id}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <StatusBadge status={item.status} />
                          </TableCell>
                          <TableCell>
                            <CategoryBadge category={item.category} />
                          </TableCell>
                          <TableCell>
                            <PriorityBadge priority={item.priority} />
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-[var(--muted-foreground)]">
                            {formatDate(item.submitted_at)}
                          </TableCell>
                          <TableCell>
                            <Value>{item.ward}</Value>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableWrapper>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
