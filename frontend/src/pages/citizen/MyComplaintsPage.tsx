/**
 * The citizen's complaint list.
 *
 * The table is server-paged, so the offset is the only state the page owns and
 * every page of results is fetched fresh rather than cached in a store.
 */

import { ClipboardList, Send } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { citizenApi } from '@/api'
import {
  CategoryBadge,
  LanguageBadge,
  PriorityBadge,
  StatusBadge,
} from '@/components/common/badges'
import { Value } from '@/components/common/DescriptionList'
import { PageHeader } from '@/components/common/PageHeader'
import { Pagination } from '@/components/common/Pagination'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  TableWrapper,
} from '@/components/ui/table'
import { formatDateTime, formatRelative } from '@/lib/format'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'

const LIMIT = 20

export default function MyComplaintsPage() {
  useDocumentTitle('My complaints')
  const navigate = useNavigate()
  const [offset, setOffset] = useState(0)

  const { data, error, isLoading, reload } = useAsyncData(
    () => citizenApi.listMine(LIMIT, offset),
    [offset],
  )

  const items = data?.items ?? []

  return (
    <div className="space-y-6">
      <PageHeader
        title="My complaints"
        description="Every complaint you have submitted, newest first. Open one to see its status timeline and your original words."
        actions={
          <Button asChild>
            <Link to="/app/submit">
              <Send className="size-4" /> Submit a complaint
            </Link>
          </Button>
        }
      />

      {isLoading ? (
        <TableWrapper>
          <TableSkeleton rows={8} />
        </TableWrapper>
      ) : error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            title="You have not submitted any complaints"
            description="When you submit a complaint it will appear here with its reference number and current status."
            icon={<ClipboardList className="size-5" />}
            action={
              <Button asChild>
                <Link to="/app/submit">
                  <Send className="size-4" /> Submit a complaint
                </Link>
              </Button>
            }
          />
        </Card>
      ) : (
        <TableWrapper>
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Reference</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Category</TableHeaderCell>
                <TableHeaderCell>Priority</TableHeaderCell>
                <TableHeaderCell>Language</TableHeaderCell>
                <TableHeaderCell>Ward</TableHeaderCell>
                <TableHeaderCell>Submitted</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.map((item) => (
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
                  <TableCell>
                    <LanguageBadge language={item.language} />
                  </TableCell>
                  <TableCell>
                    <Value>{item.ward}</Value>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <span className="text-[var(--foreground)]">{formatRelative(item.submitted_at)}</span>
                    <span className="block text-xs text-[var(--muted-foreground)]">
                      {formatDateTime(item.submitted_at)}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {data ? (
            <Pagination
              total={data.total}
              limit={data.limit}
              offset={data.offset}
              onChange={setOffset}
            />
          ) : null}
        </TableWrapper>
      )}
    </div>
  )
}
