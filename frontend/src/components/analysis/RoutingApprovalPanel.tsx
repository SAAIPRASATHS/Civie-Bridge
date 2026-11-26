/**
 * Department routing review: the AI suggestion on one side, the officer's
 * recorded decision on the other.
 *
 * The routing agent proposes a department, but a proposal is not an assignment.
 * The panel shows the recommendation as advisory and puts the only action that
 * changes the complaint - approving or rejecting the routing - behind a
 * confirmation step that records a reason in the audit trail.
 */

import { Check, X } from 'lucide-react'
import { useState } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FieldMessage, Label, Textarea } from '@/components/ui/field'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { AdvisoryNotice } from '@/components/common/AdvisoryNotice'
import { DescriptionItem, DescriptionList, Value } from '@/components/common/DescriptionList'
import {
  AiSuggestionBadge,
  ApprovedBadge,
  AssignmentStatusBadge,
} from '@/components/common/badges'
import { EmptyState } from '@/components/common/states'
import { formatDateTime, formatPercent } from '@/lib/format'
import { humanize } from '@/lib/labels'
import type { OfficerAssignmentItem, ReviewDecision, RoutingRecommendation } from '@/types'

export function RoutingApprovalPanel({
  recommendation,
  assignments,
  approvedDepartment,
  onDecide,
  canDecide,
}: {
  recommendation: RoutingRecommendation | null
  assignments: OfficerAssignmentItem[]
  approvedDepartment: { code: string | null; name: string | null } | null
  onDecide: (payload: { decision: ReviewDecision; reason: string; notes: string }) => Promise<void>
  canDecide: boolean
}) {
  const [target, setTarget] = useState<ReviewDecision | null>(null)
  const [reason, setReason] = useState('')
  const [notes, setNotes] = useState('')
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  // A routing decision only makes sense when there is a department to route to.
  const blocked = !canDecide || recommendation?.recommended_department_name == null

  const openDialog = (decision: ReviewDecision) => {
    setTarget(decision)
    setReason('')
    setNotes('')
    setFailure(null)
  }

  const confirm = async () => {
    if (!target) return
    setPending(true)
    setFailure(null)
    try {
      await onDecide({ decision: target, reason, notes })
      setTarget(null)
    } catch (problem) {
      setFailure(
        problem instanceof Error ? problem.message : 'The routing decision could not be recorded.',
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Department routing</CardTitle>
          {recommendation ? <AiSuggestionBadge /> : null}
        </div>
        <CardDescription>
          The routing agent's suggested department, and the officer decision - where one exists -
          that turns it into an assignment.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {approvedDepartment ? (
          <Alert tone="success" title="Officer decision recorded">
            <p className="flex flex-wrap items-center gap-2">
              <ApprovedBadge />
              <span>
                An officer approved routing to{' '}
                <span className="font-medium">
                  {approvedDepartment.name ?? approvedDepartment.code ?? 'a department'}
                </span>
                {approvedDepartment.name && approvedDepartment.code ? (
                  <span className="text-[var(--muted-foreground)]"> ({approvedDepartment.code})</span>
                ) : null}
                .
              </span>
            </p>
          </Alert>
        ) : null}

        {recommendation ? (
          <div className="space-y-4 rounded-lg border border-[var(--border)] p-4">
            <div className="space-y-1">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                AI-recommended department
              </p>
              <p className="text-sm">
                <span className="font-medium">
                  <Value>{recommendation.recommended_department_name}</Value>
                </span>
                {recommendation.recommended_department_code ? (
                  <span className="text-[var(--muted-foreground)]">
                    {' '}
                    ({recommendation.recommended_department_code})
                  </span>
                ) : null}
              </p>
              <p className="text-xs text-[var(--muted-foreground)]">
                Confidence {formatPercent(recommendation.confidence)} · Routing source{' '}
                {humanize(recommendation.routing_source)}
              </p>
            </div>

            {recommendation.matched_rules.length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Matched routing rules
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {recommendation.matched_rules.map((rule) => (
                    <Badge key={rule} tone="info">
                      {humanize(rule)}
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}

            {recommendation.is_prototype_data ? (
              <Alert tone="info" title="Prototype department directory">
                The directory this routing was drawn from is prototype data, so the suggested
                department is illustrative rather than a confirmed official destination.
              </Alert>
            ) : null}

            <DescriptionList columns={3}>
              <DescriptionItem label="Ward">
                <Value>{recommendation.ward}</Value>
              </DescriptionItem>
              <DescriptionItem label="Location">
                <Value>{recommendation.location_text}</Value>
              </DescriptionItem>
              <DescriptionItem label="Coordinates">
                {recommendation.has_coordinates ? 'On file' : 'Not provided'}
              </DescriptionItem>
            </DescriptionList>

            {recommendation.missing_information.length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Missing information
                </p>
                <ul className="list-disc space-y-1 pl-4 text-sm">
                  {recommendation.missing_information.map((item) => (
                    <li key={item}>{humanize(item)}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {recommendation.rationale ? (
              <div className="space-y-1.5">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Rationale
                </p>
                <p className="text-sm leading-relaxed">{recommendation.rationale}</p>
              </div>
            ) : null}

            {recommendation.requires_human_review ? (
              <Alert tone="warning" title="Flagged for human review">
                <Value>{recommendation.review_reason}</Value>
              </Alert>
            ) : null}

            <AdvisoryNotice
              notice="This department is an advisory recommendation only. Nothing has been approved until an officer records a routing decision."
              provenance={recommendation.provenance}
            />
          </div>
        ) : (
          <Alert tone="info" title="No routing recommendation has been recorded">
            The routing agent produced no department suggestion for this complaint, so it must be
            routed manually.
          </Alert>
        )}

        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <Button variant="success" disabled={blocked} onClick={() => openDialog('approve')}>
              <Check className="size-4" /> Approve routing
            </Button>
            <Button variant="outline" disabled={blocked} onClick={() => openDialog('reject')}>
              <X className="size-4" /> Reject
            </Button>
          </div>
          {!canDecide ? (
            <p className="text-xs text-[var(--muted-foreground)]">
              Only an officer can record a routing decision. You can review the recommendation but
              not act on it.
            </p>
          ) : null}
        </div>

        <div className="space-y-3">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Routing decisions on file
          </p>
          {assignments.length === 0 ? (
            <EmptyState
              title="No routing decision has been recorded"
              description="No officer has approved or rejected a department for this complaint yet."
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
                      <Value>
                        {assignment.department_name ?? assignment.department_code ?? undefined}
                      </Value>
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
        </div>
      </CardContent>

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null)
        }}
        title={target === 'approve' ? 'Approve this routing?' : 'Reject this routing?'}
        description={
          target === 'approve'
            ? `Approving assigns this complaint to ${
                recommendation?.recommended_department_name ?? 'the recommended department'
              }, sets its official department, and writes the decision to the audit trail.`
            : 'Rejecting leaves the complaint unrouted. The rejection and its reason are still written to the audit trail.'
        }
        confirmLabel={target === 'approve' ? 'Approve routing' : 'Reject routing'}
        tone={target === 'approve' ? 'destructive' : 'default'}
        onConfirm={confirm}
        pending={pending}
      >
        <div className="space-y-1.5">
          <Label htmlFor="routing-reason">Reason (recorded)</Label>
          <Textarea
            id="routing-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="e.g. This junction is the streets team's jurisdiction"
            className="min-h-20"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="routing-notes">Notes (optional)</Label>
          <Textarea
            id="routing-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            className="min-h-16"
          />
        </div>
        {failure ? <FieldMessage tone="error">{failure}</FieldMessage> : null}
      </ConfirmDialog>
    </Card>
  )
}
