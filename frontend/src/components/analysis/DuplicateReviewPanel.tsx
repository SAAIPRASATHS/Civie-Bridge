/**
 * Duplicate review: the comparison an officer uses to approve or reject a link.
 *
 * The agent's ranking is advisory. The panel separates the two facts that
 * matter: the *suggestion* (a score, the reasons behind it, the shared terms)
 * and the *decision* (which only an officer makes, through a confirmation step
 * that records a reason in the audit trail).
 */

import { Check, X } from 'lucide-react'
import { useState } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress, Separator } from '@/components/ui/skeleton'
import { FieldMessage, Label, Textarea } from '@/components/ui/field'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { CategoryBadge } from '@/components/common/badges'
import { EmptyState, ErrorState, LoadingState } from '@/components/common/states'
import { formatPercent } from '@/lib/format'
import type { DuplicateCandidate, DuplicateMatchesPage, ReviewDecision } from '@/types'

export function DuplicateReviewPanel({
  page,
  isLoading,
  error,
  onRetry,
  onDecide,
}: {
  page: DuplicateMatchesPage | null
  isLoading: boolean
  error: unknown
  onRetry: () => void
  onDecide: (candidate: DuplicateCandidate, payload: { decision: ReviewDecision; reason: string; notes: string }) => Promise<void>
}) {
  const [target, setTarget] = useState<{ candidate: DuplicateCandidate; decision: ReviewDecision } | null>(null)
  const [reason, setReason] = useState('')
  const [notes, setNotes] = useState('')
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  if (isLoading) return <LoadingState label="Comparing against stored complaints" />
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (!page) return null

  const openDialog = (candidate: DuplicateCandidate, decision: ReviewDecision) => {
    setTarget({ candidate, decision })
    setReason('')
    setNotes('')
    setFailure(null)
  }

  const confirm = async () => {
    if (!target) return
    setPending(true)
    setFailure(null)
    try {
      await onDecide(target.candidate, { decision: target.decision, reason, notes })
      setTarget(null)
    } catch (problem) {
      setFailure(problem instanceof Error ? problem.message : 'The decision could not be recorded.')
    } finally {
      setPending(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Potential duplicate complaints</CardTitle>
          <Badge tone="warning" title="Ranked by the duplicate-detection agent">
            AI suggestion
          </Badge>
        </div>
        <CardDescription>
          {page.candidates_considered} stored complaints were compared against{' '}
          <span className="font-medium">{page.reference_id}</span>. Matches are ranked by wording
          similarity, distance and category; a match above the review threshold is a candidate for
          a human decision, not a duplicate.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {page.linking_requires_human_approval ? (
          <Alert tone="warning" title="Linking requires an officer decision">
            {page.reasoning}
          </Alert>
        ) : null}

        <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
              Review threshold
            </dt>
            <dd className="font-medium">{formatPercent(page.threshold)}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
              Wording-only threshold
            </dt>
            <dd className="font-medium">{formatPercent(page.text_only_threshold)}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
              Geographic radius
            </dt>
            <dd className="font-medium">{Math.round(page.geographic_radius_m)} m</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
              Matches found
            </dt>
            <dd className="font-medium">
              {page.total_matches} of {page.candidates_considered}
            </dd>
          </div>
        </dl>

        <Separator />

        {page.items.length === 0 ? (
          <EmptyState
            title="No comparable complaint found"
            description="Nothing in the stored complaints is close enough to this wording, location and category to be worth reviewing as a duplicate."
          />
        ) : (
          <ul className="space-y-4">
            {page.items.map((candidate) => (
              <li
                key={candidate.reference_id}
                className="rounded-lg border border-[var(--border)] p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold">{candidate.reference_id}</span>
                      <CategoryBadge category={candidate.category} />
                      {candidate.is_likely_duplicate ? (
                        <Badge tone="danger" title="Scored above the review threshold">
                          Above threshold
                        </Badge>
                      ) : (
                        <Badge tone="neutral">Below threshold</Badge>
                      )}
                      {candidate.model_adjudication ? (
                        <Badge tone="info" title="The language model was asked about this pair">
                          Model adjudicated
                        </Badge>
                      ) : null}
                    </div>
                    <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
                      {candidate.text}
                    </p>
                  </div>

                  <div className="flex shrink-0 gap-2">
                    <Button
                      size="sm"
                      variant="success"
                      disabled={candidate.complaint_id === null}
                      onClick={() => openDialog(candidate, 'approve')}
                    >
                      <Check className="size-3.5" /> Approve link
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={candidate.complaint_id === null}
                      onClick={() => openDialog(candidate, 'reject')}
                    >
                      <X className="size-3.5" /> Reject
                    </Button>
                  </div>
                </div>

                {candidate.complaint_id === null ? (
                  <p className="mt-2 text-xs text-[var(--danger,var(--destructive))]">
                    This candidate could not be resolved to a stored complaint, so no decision can
                    be recorded against it.
                  </p>
                ) : null}

                <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <div className="space-y-1">
                    <p className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                      Combined score
                    </p>
                    <Progress
                      value={candidate.match_score}
                      tone={candidate.is_likely_duplicate ? 'danger' : 'info'}
                      label="Match score"
                    />
                    <p className="text-xs font-medium">{formatPercent(candidate.match_score)}</p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                      Wording similarity
                    </p>
                    <Progress value={candidate.text_similarity} tone="primary" label="Text similarity" />
                    <p className="text-xs font-medium">{formatPercent(candidate.text_similarity)}</p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                      Distance
                    </p>
                    <p className="text-sm">
                      {candidate.geographic_distance_m === null
                        ? 'Not comparable (coordinates missing)'
                        : `${Math.round(candidate.geographic_distance_m)} m apart`}
                    </p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                      Same category
                    </p>
                    <p className="text-sm">
                      {candidate.category_match === null
                        ? 'Not comparable'
                        : candidate.category_match
                          ? 'Yes'
                          : 'No'}
                    </p>
                  </div>
                </div>

                {candidate.reasons.length > 0 ? (
                  <div className="mt-4 space-y-1.5">
                    <p className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                      Why the agent ranked it here
                    </p>
                    <ul className="list-disc space-y-0.5 pl-4 text-sm">
                      {candidate.reasons.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {candidate.shared_terms.length > 0 ? (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-[var(--muted-foreground)]">Shared terms:</span>
                    {candidate.shared_terms.map((term) => (
                      <Badge key={term} tone="neutral">
                        {term}
                      </Badge>
                    ))}
                  </div>
                ) : null}

                {candidate.model_adjudication ? (
                  <p className="mt-3 text-xs text-[var(--muted-foreground)]">
                    Model note: {candidate.model_adjudication}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        <details className="rounded-lg border border-[var(--border)] p-3 text-sm">
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Scoring weights
          </summary>
          <ul className="mt-2 space-y-1">
            {Object.entries(page.weights).map(([name, weight]) => (
              <li key={name} className="flex justify-between">
                <span className="text-[var(--muted-foreground)]">{name.replace(/_/g, ' ')}</span>
                <span className="font-medium tabular-nums">{weight}</span>
              </li>
            ))}
          </ul>
        </details>
      </CardContent>

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null)
        }}
        title={
          target?.decision === 'approve'
            ? `Link ${target.candidate.reference_id} as a duplicate?`
            : `Reject the link to ${target?.candidate.reference_id ?? ''}?`
        }
        description={
          target?.decision === 'approve'
            ? 'Approving records this complaint as a duplicate of the candidate: its status becomes "Duplicate" and the link is written to the audit trail. The other complaint is not changed.'
            : 'Rejecting leaves both complaints separate. The rejection is still recorded in the audit trail.'
        }
        confirmLabel={target?.decision === 'approve' ? 'Approve link' : 'Reject link'}
        tone={target?.decision === 'approve' ? 'destructive' : 'default'}
        onConfirm={confirm}
        pending={pending}
      >
        <div className="space-y-1.5">
          <Label htmlFor="duplicate-reason">Reason (recorded)</Label>
          <Textarea
            id="duplicate-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="e.g. Same burst pipe reported twice at the same junction"
            className="min-h-20"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="duplicate-notes">Notes (optional)</Label>
          <Textarea
            id="duplicate-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            className="min-h-16"
          />
        </div>
        {failure ? (
          <FieldMessage tone="error" className="text-xs">
            {failure}
          </FieldMessage>
        ) : null}
      </ConfirmDialog>
    </Card>
  )
}
