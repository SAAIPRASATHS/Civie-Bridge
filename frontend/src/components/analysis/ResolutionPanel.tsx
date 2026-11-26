/**
 * Resolution guidance: the agent's advisory playbook, never an applied change.
 *
 * An SLA here is a prototype figure and the panel labels it as such unless the
 * server flags it verified. The status-changing language matters: the guidance
 * applies nothing itself, so the panel leads with a warning that an officer must
 * review it and act.
 */

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { AdvisoryNotice } from '@/components/common/AdvisoryNotice'
import { DescriptionItem, DescriptionList, Value } from '@/components/common/DescriptionList'
import { AiSuggestionBadge } from '@/components/common/badges'
import { formatPercent } from '@/lib/format'
import { humanize } from '@/lib/labels'
import type { ResolutionRecommendation } from '@/types'

export function ResolutionPanel({ resolution }: { resolution: ResolutionRecommendation | null }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Resolution guidance</CardTitle>
          <AiSuggestionBadge />
        </div>
        <CardDescription>
          Advisory steps for resolving this complaint. Nothing here has been applied.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {!resolution ? (
          <Alert tone="info" title="No resolution guidance has been recorded">
            The resolution agent produced no guidance for this complaint, so it must be handled
            without one.
          </Alert>
        ) : (
          <>
            <Alert tone="warning" title="Guidance only - an officer must act">
              <p>{resolution.sla_disclaimer}</p>
              <p className="mt-1 text-xs">
                No official status has been changed. This is a recommendation an officer must
                review and action.
              </p>
            </Alert>

            <DescriptionList columns={2}>
              <DescriptionItem label="Guideline">
                <Value>{resolution.guideline_title ?? resolution.guideline_code}</Value>
                {resolution.guideline_title && resolution.guideline_code ? (
                  <span className="text-[var(--muted-foreground)]">
                    {' '}
                    ({resolution.guideline_code})
                  </span>
                ) : null}
              </DescriptionItem>
              <DescriptionItem label="Department">
                <Value>{resolution.department_code}</Value>
              </DescriptionItem>
              <DescriptionItem label="Service target">
                {resolution.sla_hours === null ? (
                  <span className="text-[var(--muted-foreground)]">No SLA recorded</span>
                ) : (
                  <>
                    <span className="font-medium">Prototype SLA {resolution.sla_hours}h</span>{' '}
                    <Badge tone={resolution.sla_is_verified ? 'success' : 'warning'}>
                      {resolution.sla_is_verified ? 'Verified' : 'Unverified'}
                    </Badge>
                  </>
                )}
              </DescriptionItem>
              <DescriptionItem label="Confidence">
                {formatPercent(resolution.confidence)}
              </DescriptionItem>
            </DescriptionList>

            {resolution.sla_note ? (
              <p className="text-xs text-[var(--muted-foreground)]">SLA note: {resolution.sla_note}</p>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <LabelledList label="Resolution steps" items={resolution.resolution_steps} />
              <LabelledList label="Suggested next steps" items={resolution.suggested_next_steps} />
              <LabelledList label="Completed actions" items={resolution.completed_actions} />
              <LabelledList label="Required evidence" items={resolution.required_evidence} />
              <LabelledList label="Missing evidence" items={resolution.missing_evidence} />
            </div>

            {resolution.requires_human_review ? (
              <Alert tone="warning" title="Flagged for human review">
                <Value>{resolution.review_reason}</Value>
              </Alert>
            ) : null}

            {resolution.rationale ? (
              <div className="space-y-1.5">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Rationale
                </p>
                <p className="text-sm leading-relaxed">{resolution.rationale}</p>
              </div>
            ) : null}

            {resolution.citizen_response_template ? (
              <details className="rounded-lg border border-[var(--border)] p-3 text-sm">
                <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Suggested citizen reply (draft)
                </summary>
                <p className="mt-2 whitespace-pre-wrap leading-relaxed">
                  {resolution.citizen_response_template}
                </p>
                <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                  Draft only - nothing has been sent to the citizen.
                </p>
              </details>
            ) : null}

            {Object.keys(resolution.escalation_matrix).length > 0 ? (
              <details className="rounded-lg border border-[var(--border)] p-3 text-sm">
                <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Escalation matrix
                </summary>
                <dl className="mt-2 space-y-1">
                  {Object.entries(resolution.escalation_matrix).map(([key, value]) => (
                    <div key={key} className="flex justify-between gap-4">
                      <dt className="text-[var(--muted-foreground)]">{humanize(key)}</dt>
                      <dd className="text-right font-medium">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            ) : null}

            <AdvisoryNotice
              notice="This guidance is advisory and has not been applied. An officer must decide what, if anything, to action."
              provenance={resolution.provenance}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

/** A titled bullet list that says so plainly when the agent recorded nothing. */
function LabelledList({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
        {label}
      </p>
      {items.length === 0 ? (
        <p className="text-sm text-[var(--muted-foreground)]">None recorded.</p>
      ) : (
        <ul className="list-disc space-y-1 pl-4 text-sm">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
