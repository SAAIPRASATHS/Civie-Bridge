/**
 * The complaint queue.
 *
 * One page serves both entries in the navigation. `/officer/complaints` browses
 * everything with filters; `/officer/queue` is the same table pinned to the
 * pending-review subset. The filter values are the backend's own query
 * parameters - nothing is filtered in the browser, so what a page count says is
 * what the database holds.
 */

import { Search, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { officerApi } from '@/api'
import { PageHeader } from '@/components/common/PageHeader'
import { Pagination } from '@/components/common/Pagination'
import { CategoryBadge, PriorityBadge, StatusBadge } from '@/components/common/badges'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/common/states'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input, Label, Select } from '@/components/ui/field'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  TableWrapper,
} from '@/components/ui/table'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'
import { formatDate, formatRelative } from '@/lib/format'
import { categoryLabel, priorityLabel, statusLabel } from '@/lib/labels'
import {
  COMPLAINT_CATEGORIES,
  COMPLAINT_PRIORITIES,
  COMPLAINT_STATUSES,
} from '@/types'
import type { ComplaintCategory, ComplaintPriority, ComplaintStatus, OfficerComplaintFilters } from '@/types'

const PAGE_SIZE = 20

/**
 * Delay applying a keystroke to the query so typing does not issue one request
 * per character. 350 ms is long enough to batch a burst of typing and short
 * enough to feel immediate.
 */
function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export default function OfficerQueuePage({ pendingOnly = false }: { pendingOnly?: boolean }) {
  useDocumentTitle(pendingOnly ? 'Review queue' : 'Complaints')

  const navigate = useNavigate()
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<'' | ComplaintStatus>('')
  const [priority, setPriority] = useState<'' | ComplaintPriority>('')
  const [category, setCategory] = useState<'' | ComplaintCategory>('')
  const [reviewOnly, setReviewOnly] = useState(pendingOnly)

  const debouncedSearch = useDebounced(search)

  // Any filter change invalidates the current page: returning to page 3 of a
  // narrowed result set would otherwise show an empty page.
  const changeFilter = (apply: () => void) => {
    apply()
    setOffset(0)
  }

  const filters: OfficerComplaintFilters = {
    status: status ? [status] : undefined,
    priority: priority || undefined,
    category: category || undefined,
    pending_review_only: reviewOnly || undefined,
    search: debouncedSearch.trim() || undefined,
    limit: PAGE_SIZE,
    offset,
  }

  const { data, error, isLoading, reload } = useAsyncData(
    () => officerApi.listComplaints(filters),
    [
      status,
      priority,
      category,
      reviewOnly,
      debouncedSearch,
      offset,
    ],
  )

  const activeFilters = Boolean(status || priority || category || reviewOnly || search.trim())

  const clearFilters = () => {
    setSearch('')
    setStatus('')
    setPriority('')
    setCategory('')
    setReviewOnly(pendingOnly)
    setOffset(0)
  }

  return (
    <>
      <PageHeader
        eyebrow="Officer dashboard"
        title={pendingOnly ? 'Review queue' : 'Complaints'}
        description={
          pendingOnly
            ? 'Every complaint that does not yet carry an officer decision.'
            : 'All stored complaints. Filter by status, priority, category or text to find the one you need.'
        }
      />

      <Card>
        <CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4">
          <div className="space-y-1.5 sm:col-span-2 xl:col-span-1">
            <Label htmlFor="queue-search">Search</Label>
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]"
                aria-hidden
              />
              <Input
                id="queue-search"
                value={search}
                className="pl-9"
                placeholder="Reference id or location"
                onChange={(event) => changeFilter(() => setSearch(event.target.value))}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="queue-status">Status</Label>
            <Select
              id="queue-status"
              value={status}
              onChange={(event) =>
                changeFilter(() => setStatus(event.target.value as '' | ComplaintStatus))
              }
            >
              <option value="">Any status</option>
              {COMPLAINT_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {statusLabel(value)}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="queue-priority">Priority</Label>
            <Select
              id="queue-priority"
              value={priority}
              onChange={(event) =>
                changeFilter(() => setPriority(event.target.value as '' | ComplaintPriority))
              }
            >
              <option value="">Any priority</option>
              {COMPLAINT_PRIORITIES.map((value) => (
                <option key={value} value={value}>
                  {priorityLabel(value)}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="queue-category">Category</Label>
            <Select
              id="queue-category"
              value={category}
              onChange={(event) =>
                changeFilter(() => setCategory(event.target.value as '' | ComplaintCategory))
              }
            >
              <option value="">Any category</option>
              {COMPLAINT_CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {categoryLabel(value)}
                </option>
              ))}
            </Select>
          </div>

          <div className="flex items-end gap-3 sm:col-span-2 xl:col-span-4">
            {pendingOnly ? null : (
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4 rounded border-[var(--input)]"
                  checked={reviewOnly}
                  onChange={(event) => changeFilter(() => setReviewOnly(event.target.checked))}
                />
                Awaiting a decision only
              </label>
            )}
            {activeFilters ? (
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                <X className="size-3.5" /> Clear filters
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {data ? (
        <Alert tone="info" title="Advisory content">
          {data.suggestion_notice}
        </Alert>
      ) : null}

      {isLoading ? (
        <TableSkeleton rows={8} />
      ) : error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : data && data.items.length === 0 ? (
        <EmptyState
          title="No complaints match these filters"
          description={
            activeFilters
              ? 'Try widening the filters, or clear them to see the whole queue.'
              : 'No complaints have been recorded yet.'
          }
          action={
            activeFilters ? (
              <Button variant="outline" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            ) : undefined
          }
        />
      ) : data ? (
        <>
          <TableWrapper>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Reference</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Priority</TableHeaderCell>
                  <TableHeaderCell>Category</TableHeaderCell>
                  <TableHeaderCell>Department</TableHeaderCell>
                  <TableHeaderCell>Evidence</TableHeaderCell>
                  <TableHeaderCell>Reported</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.items.map((item) => (
                  <TableRow
                    key={item.complaint_id}
                    interactive
                    onClick={() => navigate(`/officer/complaints/${item.complaint_id}`)}
                  >
                    <TableCell>
                      <Link
                        to={`/officer/complaints/${item.complaint_id}`}
                        className="font-medium text-[var(--primary)] hover:underline"
                        onClick={(event) => event.stopPropagation()}
                      >
                        {item.reference_id}
                      </Link>
                      {item.needs_clarification ? (
                        <span className="mt-1 block text-xs text-[var(--warning)]">
                          Clarification requested
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={item.status} />
                    </TableCell>
                    <TableCell>
                      <PriorityBadge priority={item.priority} />
                    </TableCell>
                    <TableCell>
                      <CategoryBadge category={item.category} />
                    </TableCell>
                    <TableCell>
                      {item.recommended_department_name ? (
                        <span className="flex flex-col gap-1">
                          <span className="text-sm">{item.recommended_department_name}</span>
                          {item.department_is_approved ? (
                            <Badge tone="success" title="Confirmed by an officer">
                              Officer-approved
                            </Badge>
                          ) : (
                            <Badge tone="warning" title="Advisory agent output">
                              AI suggestion
                            </Badge>
                          )}
                        </span>
                      ) : (
                        <span className="text-[var(--muted-foreground)]">Not routed</span>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">{item.evidence_count}</TableCell>
                    <TableCell>
                      <span className="flex flex-col">
                        <span>{formatDate(item.reported_at ?? item.submitted_at)}</span>
                        <span className="text-xs text-[var(--muted-foreground)]">
                          {formatRelative(item.reported_at ?? item.submitted_at)}
                        </span>
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableWrapper>

          <Pagination
            total={data.total}
            limit={data.limit}
            offset={data.offset}
            onChange={setOffset}
          />
        </>
      ) : null}
    </>
  )
}
