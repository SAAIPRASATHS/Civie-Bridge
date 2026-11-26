import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/skeleton'
import { DescriptionItem, DescriptionList, Value } from '@/components/common/DescriptionList'
import { AiSuggestionBadge } from '@/components/common/badges'
import { formatPercent } from '@/lib/format'
import { languageLabel } from '@/lib/labels'
import type { IntakeResult } from '@/types'

/**
 * The intake agent's structured understanding of the complaint.
 *
 * `missing_information` and `clarification_questions` are shown prominently:
 * they are the agent's honest statement of what it could not establish, and
 * they are what an officer acts on when deciding whether to seek clarification.
 */
export function IntakePanel({
  intake,
  originalText,
}: {
  intake: IntakeResult | null
  originalText: string
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Intake</CardTitle>
          <AiSuggestionBadge />
        </div>
        <CardDescription>
          The complaint as recorded, and the agent&apos;s normalised reading of it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Citizen&apos;s words (verbatim)
          </p>
          <blockquote className="rounded-md border-l-4 border-[var(--primary)] bg-[var(--primary-subtle)] px-4 py-3 text-sm leading-relaxed">
            {originalText}
          </blockquote>
        </div>

        {!intake ? (
          <Alert tone="info" title="No intake result recorded">
            The complaint is stored verbatim but the intake agent produced no structured
            output, so it needs manual triage.
          </Alert>
        ) : (
          <>
            <div className="space-y-1.5">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Normalised restatement
              </p>
              <p className="text-sm leading-relaxed">{intake.normalized_description}</p>
            </div>

            <DescriptionList columns={2}>
              <DescriptionItem label="Detected language">
                {languageLabel(intake.language)}{' '}
                <span className="text-[var(--muted-foreground)]">
                  ({formatPercent(intake.language_confidence)} confidence)
                </span>
              </DescriptionItem>
              <DescriptionItem label="Problem type">
                <Value>{intake.problem_type?.replace(/_/g, ' ')}</Value>
              </DescriptionItem>
              <DescriptionItem label="Ward / zone">
                <Value>{intake.ward}</Value>
              </DescriptionItem>
              <DescriptionItem label="Location mentioned">
                <Value>{intake.location_text}</Value>
              </DescriptionItem>
              <DescriptionItem label="Duration" >
                <Value>{intake.duration_text ?? (intake.duration_days !== null ? `${intake.duration_days} days` : null)}</Value>
              </DescriptionItem>
              <DescriptionItem label="Overall confidence">
                {formatPercent(intake.confidence)}
              </DescriptionItem>
            </DescriptionList>

            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Understanding confidence
              </p>
              <Progress value={intake.confidence} label="Intake confidence" />
            </div>

            {intake.landmarks.length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Landmarks
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {intake.landmarks.map((landmark) => (
                    <Badge key={landmark} tone="neutral">
                      {landmark}
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Missing information
                </p>
                {intake.missing_information.length === 0 ? (
                  <p className="text-sm text-[var(--muted-foreground)]">
                    Nothing identified as missing.
                  </p>
                ) : (
                  <ul className="list-disc space-y-1 pl-4 text-sm">
                    {intake.missing_information.map((item) => (
                      <li key={item}>{item.replace(/_/g, ' ')}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  Suggested clarification questions
                </p>
                {intake.clarification_questions.length === 0 ? (
                  <p className="text-sm text-[var(--muted-foreground)]">
                    No clarification suggested.
                  </p>
                ) : (
                  <ul className="list-disc space-y-1 pl-4 text-sm">
                    {intake.clarification_questions.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            <div className="space-y-1.5">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Agent reasoning
              </p>
              <p className="text-sm leading-relaxed">{intake.reasoning}</p>
            </div>

            {intake.unsupported_details_dropped.length > 0 ? (
              <Alert tone="info" title="Claims discarded as untraceable">
                <ul className="list-disc space-y-1 pl-4">
                  {intake.unsupported_details_dropped.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </Alert>
            ) : null}

            {intake.degraded ? (
              <Alert tone="warning" title="Deterministic fallback was used">
                The language model was unavailable for this step, so a rule-based result was
                recorded instead.
              </Alert>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}
