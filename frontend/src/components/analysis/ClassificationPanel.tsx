import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress, Separator } from '@/components/ui/skeleton'
import { DescriptionItem, DescriptionList, Value } from '@/components/common/DescriptionList'
import { AiSuggestionBadge, CategoryBadge, PriorityBadge } from '@/components/common/badges'
import { Alert } from '@/components/ui/alert'
import { formatPercent } from '@/lib/format'
import { priorityLabel } from '@/lib/labels'
import type { ClassificationResult } from '@/types'

/**
 * The classification agent's full output, presented as a suggestion.
 *
 * The panel is explicit about what the agent could not determine: a null
 * category is shown as "Unclassified" and paired with the agent's
 * `needs_human_review` flag, rather than being hidden or guessed at.
 */
export function ClassificationPanel({
  classification,
  approvedDepartment,
}: {
  classification: ClassificationResult | null
  approvedDepartment?: { code: string | null; name: string | null } | null
}) {
  if (!classification) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Classification</CardTitle>
          <CardDescription>No classification result has been recorded.</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const escalated =
    classification.escalation_reason !== null && classification.escalation_reason !== undefined

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Classification</CardTitle>
          <AiSuggestionBadge />
        </div>
        <CardDescription>
          Category, priority and the routing the classification agent proposed.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <CategoryBadge category={classification.category} />
          <PriorityBadge priority={classification.priority} />
          {classification.needs_human_review ? (
            <Badge tone="warning">Needs human review</Badge>
          ) : null}
          {classification.degraded ? <Badge tone="neutral">Deterministic fallback</Badge> : null}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
            <span>Classification confidence</span>
            <span className="font-medium text-[var(--foreground)]">
              {formatPercent(classification.confidence)}
            </span>
          </div>
          <Progress
            value={classification.confidence}
            tone={classification.confidence >= 0.7 ? 'success' : classification.confidence >= 0.45 ? 'warning' : 'danger'}
            label="Classification confidence"
          />
        </div>

        <DescriptionList columns={2}>
          <DescriptionItem label="Urgency score">
            {formatPercent(classification.urgency_score)}
          </DescriptionItem>
          <DescriptionItem label="Subcategory">
            <Value>{classification.subcategory}</Value>
          </DescriptionItem>
          <DescriptionItem label="Decision source">
            {classification.llm_used ? 'Language model + deterministic rules' : 'Deterministic rules only'}
          </DescriptionItem>
          <DescriptionItem label="Model">
            <Value>{classification.model_name}</Value>
          </DescriptionItem>
        </DescriptionList>

        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Reasoning
          </p>
          <p className="text-sm leading-relaxed">{classification.reasoning}</p>
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Recommended next action
          </p>
          <p className="text-sm leading-relaxed">{classification.recommended_next_action}</p>
        </div>

        {escalated ? (
          <Alert tone="warning" title="Priority was escalated above the keyword evidence">
            <p>{classification.escalation_reason}</p>
            <p className="mt-1 text-xs">
              Keyword evidence alone supported{' '}
              <span className="font-medium">{priorityLabel(classification.deterministic_priority)}</span>
              {classification.model_priority ? (
                <>
                  ; the model proposed{' '}
                  <span className="font-medium">{priorityLabel(classification.model_priority)}</span>
                </>
              ) : null}
              . The applied band is{' '}
              <span className="font-medium">{priorityLabel(classification.priority)}</span>.
            </p>
          </Alert>
        ) : null}

        {classification.needs_human_review && classification.review_reason ? (
          <Alert tone="warning" title="Flagged for human review">
            {classification.review_reason}
          </Alert>
        ) : null}

        {classification.matched_signals.length > 0 ? (
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              Matched keyword evidence
            </p>
            <div className="flex flex-wrap gap-1.5">
              {classification.matched_signals.map((signal) => (
                <Badge key={signal} tone="info">
                  {signal.replace(/_/g, ' ')}
                </Badge>
              ))}
            </div>
          </div>
        ) : null}

        <Separator />

        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              Department suggested by the classification agent
            </p>
            {approvedDepartment ? (
              <Badge tone="success">Officer-approved routing on file</Badge>
            ) : null}
          </div>
          <p className="text-sm">
            <Value>{classification.department_name ?? classification.department_code}</Value>
            {classification.sla_hours !== null ? (
              <span className="text-[var(--muted-foreground)]">
                {' '}
                - prototype SLA {classification.sla_hours}h
              </span>
            ) : null}
          </p>
          <p className="text-xs text-[var(--muted-foreground)]">
            Source: {classification.routing_source.replace(/_/g, ' ')}
          </p>
          {approvedDepartment && approvedDepartment.name !== classification.department_name ? (
            <p className="text-xs text-[var(--warning)]">
              An officer approved a different department: {approvedDepartment.name}.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
