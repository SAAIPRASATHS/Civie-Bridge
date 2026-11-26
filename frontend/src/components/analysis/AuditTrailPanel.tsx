/**
 * The append-only audit history for one complaint.
 *
 * Status changes, officer routing decisions and agent steps are the three things
 * a review needs to reconstruct. Everything officer-made is recorded here, so
 * this panel is the single place the official record is read back: a status
 * badge is the stored status, an "an officer" attribution is a human action.
 */

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  TableWrapper,
} from '@/components/ui/table'
import { AssignmentStatusBadge, StatusBadge } from '@/components/common/badges'
import { EmptyState } from '@/components/common/states'
import { AgentStepsPanel } from '@/components/analysis/AgentStepsPanel'
import { formatDateTime, formatPercent } from '@/lib/format'
import { humanize } from '@/lib/labels'
import type {
  ComplaintStatus,
  OfficerAgentStep,
  OfficerAssignmentItem,
  OfficerStatusEvent,
} from '@/types'

export function AuditTrailPanel({
  statusHistory,
  assignments,
  agentSteps,
  currentStatus,
}: {
  statusHistory: OfficerStatusEvent[]
  assignments: OfficerAssignmentItem[]
  agentSteps: OfficerAgentStep[]
  currentStatus: ComplaintStatus
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit history</CardTitle>
        <CardDescription>
          An append-only record of every status change, officer routing decision and agent step for
          this complaint. Every officer decision is written here.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-6">
        <section className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold">Status history</h4>
            <span className="text-xs text-[var(--muted-foreground)]">Current status:</span>
            <StatusBadge status={currentStatus} />
          </div>
          {statusHistory.length === 0 ? (
            <EmptyState
              title="No status changes recorded"
              description="No status change has been written for this complaint yet."
            />
          ) : (
            <TableWrapper>
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>From</TableHeaderCell>
                    <TableHeaderCell>To</TableHeaderCell>
                    <TableHeaderCell>Reason</TableHeaderCell>
                    <TableHeaderCell>Notes</TableHeaderCell>
                    <TableHeaderCell>Recorded by</TableHeaderCell>
                    <TableHeaderCell>Changed at</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {statusHistory.map((event) => (
                    <TableRow key={event.history_id}>
                      <TableCell>
                        {event.from_status ? (
                          <StatusBadge status={event.from_status} />
                        ) : (
                          <span className="text-[var(--muted-foreground)]">New</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={event.to_status} />
                      </TableCell>
                      <TableCell>{event.reason ?? '-'}</TableCell>
                      <TableCell className="text-[var(--muted-foreground)]">
                        {event.notes ?? '-'}
                      </TableCell>
                      <TableCell>{describeActor(event)}</TableCell>
                      <TableCell className="whitespace-nowrap text-[var(--muted-foreground)]">
                        {formatDateTime(event.changed_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableWrapper>
          )}
        </section>

        <section className="space-y-3">
          <h4 className="text-sm font-semibold">Routing decisions</h4>
          {assignments.length === 0 ? (
            <EmptyState
              title="No routing decisions recorded"
              description="No officer has approved or rejected a department for this complaint."
            />
          ) : (
            <ul className="space-y-3">
              {assignments.map((assignment) => (
                <li
                  key={assignment.assignment_id}
                  className="space-y-2 rounded-lg border border-[var(--border)] p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium">
                      {assignment.department_name ?? assignment.department_code ?? 'No department'}
                      {assignment.department_name && assignment.department_code ? (
                        <span className="text-[var(--muted-foreground)]">
                          {' '}
                          ({assignment.department_code})
                        </span>
                      ) : null}
                    </span>
                    <AssignmentStatusBadge status={assignment.approval_status} />
                  </div>
                  {assignment.ai_recommended_department_code ? (
                    <p className="text-xs text-[var(--muted-foreground)]">
                      AI suggestion:{' '}
                      <span className="font-medium">
                        {assignment.ai_recommended_department_code}
                      </span>{' '}
                      ({formatPercent(assignment.ai_confidence)})
                    </p>
                  ) : null}
                  {assignment.approved_at ? (
                    <p className="text-xs text-[var(--muted-foreground)]">
                      Approved {formatDateTime(assignment.approved_at)}
                    </p>
                  ) : null}
                  {assignment.rejection_reason ? (
                    <p className="text-sm">
                      <span className="text-[var(--muted-foreground)]">Reason: </span>
                      {assignment.rejection_reason}
                    </p>
                  ) : null}
                  {assignment.notes ? (
                    <p className="text-xs text-[var(--muted-foreground)]">
                      Notes: {assignment.notes}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-3">
          <h4 className="text-sm font-semibold">Agent steps</h4>
          {agentSteps.length === 0 ? (
            <EmptyState
              title="No agent steps recorded"
              description="No agent activity has been recorded for this complaint."
            />
          ) : (
            <AgentStepsPanel steps={agentSteps} />
          )}
        </section>
      </CardContent>
    </Card>
  )
}

/** Attribute a status change: an agent, a human officer, or the system. */
function describeActor(event: OfficerStatusEvent): string {
  if (event.changed_by_agent) return humanize(event.changed_by_agent)
  if (event.changed_by_user_id) return 'An officer'
  return 'System'
}
