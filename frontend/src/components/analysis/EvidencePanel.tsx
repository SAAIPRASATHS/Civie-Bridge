/**
 * Evidence verification: what the vision agent measured, and what it only assumed.
 *
 * The two are kept visually apart. "Observable findings" are measurements taken
 * from the image; "Assumptions" are pinned to their basis and source so an
 * officer can weigh them. A photo is never presented as proof that the complaint
 * is valid - that judgement belongs to an officer, and the panel says so.
 */

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert } from '@/components/ui/alert'
import { Progress } from '@/components/ui/skeleton'
import { AiSuggestionBadge, EvidenceStatusBadge } from '@/components/common/badges'
import { EmptyState } from '@/components/common/states'
import { formatBytes, formatDateTime, formatPercent } from '@/lib/format'
import type { EvidenceAnalysisResult, OfficerEvidenceItem } from '@/types'

export function EvidencePanel({ evidence }: { evidence: OfficerEvidenceItem[] }) {
  const hasAnalysis = evidence.some((item) => item.analysis !== null)

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Evidence and image analysis</CardTitle>
          {hasAnalysis ? <AiSuggestionBadge /> : null}
        </div>
        <CardDescription>
          Each attachment, the verification status recorded for it, and - where the vision agent
          ran - its measurements, assumptions and limits.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {evidence.length === 0 ? (
          <EmptyState
            title="No photos were attached"
            description="This complaint was submitted without any attachments, so there is nothing for the vision agent to analyse."
          />
        ) : (
          <ul className="space-y-5">
            {evidence.map((item) => {
              const analysis = item.analysis
              return (
                <li
                  key={item.evidence_id}
                  className="space-y-4 rounded-lg border border-[var(--border)] p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <p className="truncate text-sm font-semibold">{item.file_name}</p>
                      <p className="text-xs text-[var(--muted-foreground)]">
                        {item.mime_type ?? 'Type not recorded'} · {formatBytes(item.file_size_bytes)}{' '}
                        · uploaded {formatDateTime(item.uploaded_at)}
                      </p>
                      {item.caption ? (
                        <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
                          {item.caption}
                        </p>
                      ) : null}
                    </div>
                    <EvidenceStatusBadge status={item.verification_status} />
                  </div>

                  {item.relevance_score !== null ? (
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
                        <span>Relevance to the complaint</span>
                        <span className="font-medium text-[var(--foreground)]">
                          {formatPercent(item.relevance_score)}
                        </span>
                      </div>
                      <Progress
                        value={item.relevance_score}
                        tone={
                          item.relevance_score >= 0.6
                            ? 'success'
                            : item.relevance_score >= 0.35
                              ? 'warning'
                              : 'danger'
                        }
                        label="Evidence relevance"
                      />
                    </div>
                  ) : null}

                  {item.requires_manual_review ? (
                    <Alert tone="warning" title="Needs an officer's review">
                      This attachment was flagged for manual review and has not been accepted on its
                      own.
                    </Alert>
                  ) : null}

                  {item.verification_notes ? (
                    <p className="text-xs text-[var(--muted-foreground)]">{item.verification_notes}</p>
                  ) : null}

                  {item.agent_explanation ? (
                    <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
                      {item.agent_explanation}
                    </p>
                  ) : null}

                  {analysis ? <AnalysisBlocks analysis={analysis} /> : null}
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/** The structured half of the vision agent's output, rendered as separated blocks. */
function AnalysisBlocks({ analysis }: { analysis: EvidenceAnalysisResult }) {
  return (
    <div className="space-y-4 border-t border-[var(--border)] pt-4">
      {analysis.is_proof_of_validity === false ? (
        <Alert tone="warning" title="This photo is not proof the complaint is valid">
          The agent can describe what is visible, but an image on its own does not establish that
          the issue is real, current or the council's responsibility. An officer must judge whether
          this complaint is valid.
        </Alert>
      ) : null}

      {analysis.observable_findings.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Observable findings (measured from the image)
          </p>
          <ul className="space-y-2">
            {analysis.observable_findings.map((finding, index) => (
              <li key={index} className="text-sm">
                <p>{finding.statement}</p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  {finding.kind.replace(/_/g, ' ')}
                  {finding.measured_value ? ` · ${finding.measured_value}` : ''} ·{' '}
                  {formatPercent(finding.confidence)} confidence
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {analysis.assumptions.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Assumptions (not verified)
          </p>
          <ul className="space-y-2">
            {analysis.assumptions.map((assumption, index) => (
              <li key={index} className="text-sm">
                <p>{assumption.statement}</p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  Basis: {assumption.basis} · Source: {assumption.source} ·{' '}
                  {formatPercent(assumption.confidence)} confidence
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {analysis.limitations.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Limitations
          </p>
          <ul className="list-disc space-y-1 pl-4 text-sm">
            {analysis.limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {analysis.unsupported_claims_dropped.length > 0 ? (
        <Alert tone="info" title="Claims discarded as unsupported">
          <ul className="list-disc space-y-1 pl-4">
            {analysis.unsupported_claims_dropped.map((claim, index) => (
              <li key={index}>{claim}</li>
            ))}
          </ul>
        </Alert>
      ) : null}

      {analysis.verification_notes ? (
        <Prose label="Verification notes">{analysis.verification_notes}</Prose>
      ) : null}
      {analysis.reasoning ? <Prose label="Reasoning">{analysis.reasoning}</Prose> : null}
      {analysis.recommendation ? (
        <Prose label="Recommendation">{analysis.recommendation}</Prose>
      ) : null}

      <details className="rounded-lg border border-[var(--border)] p-3 text-sm">
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
          Image quality measurements
        </summary>
        <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
          <QualityMetric label="Width (px)" value={analysis.width} precision={0} />
          <QualityMetric label="Height (px)" value={analysis.height} precision={0} />
          <QualityMetric label="Blur score" value={analysis.blur_score} />
          <QualityMetric label="Brightness" value={analysis.brightness} />
          <QualityMetric label="Contrast" value={analysis.contrast} />
          <QualityMetric label="Dark pixel ratio" value={analysis.dark_pixel_ratio} />
          <QualityMetric label="Edge density" value={analysis.edge_density} />
        </dl>
      </details>

      <p className="text-xs text-[var(--muted-foreground)]">
        {analysis.model_name ? `${analysis.model_name} · ` : ''}
        {analysis.latency_ms !== null ? `${analysis.latency_ms} ms` : 'latency not recorded'}
      </p>
    </div>
  )
}

function Prose({ label, children }: { label: string; children: string }) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
        {label}
      </p>
      <p className="text-sm leading-relaxed">{children}</p>
    </div>
  )
}

function QualityMetric({
  label,
  value,
  precision = 3,
}: {
  label: string
  value: number | null
  precision?: number
}) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">{label}</dt>
      <dd className="text-sm tabular-nums">{value === null ? '-' : value.toFixed(precision)}</dd>
    </div>
  )
}
