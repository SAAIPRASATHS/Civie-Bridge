/**
 * Complaint submission.
 *
 * The form collects the citizen's own words first and everything else as
 * optional hints. The AI output is rendered as a suggestion throughout, and the
 * page never offers a decision control: an officer alone decides. The result is
 * shown in place of the form so the citizen keeps their reference number on
 * screen instead of being navigated away.
 */

import { Plus, Send, ShieldCheck, Sparkles } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'

import { ApiError, citizenApi } from '@/api'
import { ClassificationPanel } from '@/components/analysis/ClassificationPanel'
import { IntakePanel } from '@/components/analysis/IntakePanel'
import { EvidenceUploader, toEvidencePayload } from '@/components/citizen/EvidenceUploader'
import type { SelectedPhoto } from '@/components/citizen/EvidenceUploader'
import { AiSuggestionBadge, StatusBadge } from '@/components/common/badges'
import { AdvisoryNotice } from '@/components/common/AdvisoryNotice'
import { DescriptionItem, DescriptionList, Value } from '@/components/common/DescriptionList'
import { PageHeader } from '@/components/common/PageHeader'
import { LocationPicker } from '@/components/maps/LocationPicker'
import type { LatLng } from '@/components/maps/LocationPicker'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { FieldMessage, Input, Label, Select, Textarea } from '@/components/ui/field'
import { formatPercent, truncate } from '@/lib/format'
import { useDocumentTitle } from '@/lib/hooks'
import { CATEGORY_LABELS, LANGUAGE_LABELS } from '@/lib/labels'
import { COMPLAINT_CATEGORIES, COMPLAINT_LANGUAGES } from '@/types'
import type {
  ComplaintCategory,
  ComplaintLanguage,
  ComplaintSubmissionRequest,
  ComplaintSubmissionResponse,
  DuplicateCandidate,
  ResolutionRecommendation,
  RoutingRecommendation,
} from '@/types'

/** The routing agent's suggestion, presented read-only. */
function RoutingSummary({ routing }: { routing: RoutingRecommendation | null }) {
  if (!routing) return null
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Suggested department</CardTitle>
          <AiSuggestionBadge />
        </div>
        <CardDescription>
          Where the routing agent suggests the complaint goes. An officer confirms the department.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <DescriptionList columns={2}>
          <DescriptionItem label="Suggested department">
            <Value>{routing.recommended_department_name ?? routing.recommended_department_code}</Value>
          </DescriptionItem>
          <DescriptionItem label="Confidence">{formatPercent(routing.confidence)}</DescriptionItem>
        </DescriptionList>
        <p className="text-sm leading-relaxed">{routing.rationale}</p>
        {routing.requires_human_review ? (
          <p className="text-xs text-[var(--warning)]">
            {routing.review_reason ?? 'This routing needs officer review.'}
          </p>
        ) : null}
        {routing.is_prototype_data ? (
          <p className="text-xs text-[var(--muted-foreground)]">Based on prototype routing data.</p>
        ) : null}
      </CardContent>
    </Card>
  )
}

/** The resolution agent's draft, presented read-only. */
function ResolutionSummary({ resolution }: { resolution: ResolutionRecommendation | null }) {
  if (!resolution) return null
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Suggested resolution</CardTitle>
          <AiSuggestionBadge />
        </div>
        <CardDescription>
          A draft of how the complaint might be resolved. It is a recommendation, not an instruction.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <DescriptionList columns={2}>
          <DescriptionItem label="Guideline">
            <Value>{resolution.guideline_title ?? resolution.guideline_code}</Value>
          </DescriptionItem>
          <DescriptionItem label="Prototype SLA">
            <Value>{resolution.sla_hours !== null ? `${resolution.sla_hours} hours` : null}</Value>
          </DescriptionItem>
        </DescriptionList>
        {resolution.resolution_steps.length > 0 ? (
          <div className="space-y-1.5">
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              Proposed steps
            </p>
            <ol className="list-decimal space-y-1 pl-5 text-sm">
              {resolution.resolution_steps.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          </div>
        ) : null}
        <p className="text-sm leading-relaxed">{resolution.rationale}</p>
        {resolution.sla_disclaimer ? (
          <p className="text-xs text-[var(--muted-foreground)]">{resolution.sla_disclaimer}</p>
        ) : null}
      </CardContent>
    </Card>
  )
}

/** Candidate duplicates the agents flagged, presented read-only. */
function DuplicateSummary({ candidates }: { candidates: DuplicateCandidate[] }) {
  if (candidates.length === 0) return null
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Possible duplicates</CardTitle>
          <AiSuggestionBadge />
        </div>
        <CardDescription>
          Other complaints that may describe the same problem. An officer decides whether to link
          them.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-[var(--border)]">
          {candidates.map((candidate) => (
            <li key={candidate.reference_id} className="space-y-1 py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{candidate.reference_id}</span>
                <Badge tone={candidate.is_likely_duplicate ? 'warning' : 'neutral'}>
                  {candidate.is_likely_duplicate ? 'Likely duplicate' : 'Possible match'} ·{' '}
                  {formatPercent(candidate.match_score)}
                </Badge>
              </div>
              <p className="text-sm text-[var(--muted-foreground)]">
                {truncate(candidate.text, 160)}
              </p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

export default function SubmitComplaintPage() {
  useDocumentTitle('Submit a complaint')

  const [text, setText] = useState('')
  const [language, setLanguage] = useState<ComplaintLanguage | ''>('')
  const [category, setCategory] = useState<ComplaintCategory | ''>('')
  const [locationText, setLocationText] = useState('')
  const [ward, setWard] = useState('')
  const [coords, setCoords] = useState<LatLng | null>(null)
  const [photos, setPhotos] = useState<SelectedPhoto[]>([])

  const [clientError, setClientError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [result, setResult] = useState<ComplaintSubmissionResponse | null>(null)

  // The id is fixed for the life of the form so that a retry after a network
  // failure is idempotent: the backend recognises the repeated id and returns
  // the complaint it already created rather than recording a second one.
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())

  const fieldErrors = error instanceof ApiError ? error.fieldErrors : {}

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!text.trim()) {
      setClientError('Describe the problem before submitting.')
      return
    }
    setClientError(null)
    setError(null)
    setSubmitting(true)
    try {
      const payload: ComplaintSubmissionRequest = {
        text,
        language: language || null,
        category: category || null,
        location_text: locationText || null,
        ward: ward || null,
        latitude: coords?.latitude ?? null,
        longitude: coords?.longitude ?? null,
        evidence: await toEvidencePayload(photos),
        request_id: requestId,
      }
      const response = await citizenApi.submit(payload)
      setResult(response)
      toast.success('Complaint submitted')
    } catch (failure) {
      setError(failure instanceof Error ? failure : new Error('Submission failed.'))
      toast.error('Could not submit the complaint')
    } finally {
      setSubmitting(false)
    }
  }

  const resetForm = () => {
    // Revoke the preview URLs the uploader created, then clear every field and
    // mint a fresh request id so the next complaint is a distinct submission.
    photos.forEach((photo) => URL.revokeObjectURL(photo.previewUrl))
    setText('')
    setLanguage('')
    setCategory('')
    setLocationText('')
    setWard('')
    setCoords(null)
    setPhotos([])
    setClientError(null)
    setError(null)
    setResult(null)
    setRequestId(crypto.randomUUID())
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Submit a complaint"
        description="Describe the problem in your own words. The AI will suggest how it should be categorised and routed, and an officer will make the final decision."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-6">
          {result ? (
            <>
              <Card>
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <CardTitle>Complaint submitted</CardTitle>
                    <StatusBadge status={result.complaint.status} />
                  </div>
                  <CardDescription>
                    Keep this reference number to track your complaint.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="rounded-lg border border-[var(--border)] bg-[var(--primary-subtle)] px-4 py-3">
                    <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                      Reference
                    </p>
                    <p className="text-2xl font-semibold tracking-tight text-[var(--primary)]">
                      {result.complaint.reference_id}
                    </p>
                  </div>
                  {result.message ? (
                    <p className="text-sm text-[var(--muted-foreground)]">{result.message}</p>
                  ) : null}
                  {result.replayed ? (
                    <Alert tone="info" title="Already recorded">
                      This submission had been received before, so the existing complaint was
                      returned rather than a new one being created.
                    </Alert>
                  ) : null}
                </CardContent>
                <CardFooter>
                  <Button asChild>
                    <Link to={`/app/complaints/${result.complaint.reference_id}`}>
                      Track this complaint
                    </Link>
                  </Button>
                  <Button variant="outline" onClick={resetForm}>
                    <Plus className="size-4" /> Submit another
                  </Button>
                </CardFooter>
              </Card>

              <AdvisoryNotice
                notice={result.suggestion_notice}
                provenance={result.provenance}
                title="The analysis below is advisory"
              />

              {result.analysis ? (
                <>
                  <IntakePanel
                    intake={result.analysis.intake ?? null}
                    originalText={result.original_text}
                  />
                  <ClassificationPanel classification={result.analysis.classification ?? null} />
                  <RoutingSummary routing={result.analysis.routing} />
                  <ResolutionSummary resolution={result.analysis.resolution} />
                  <DuplicateSummary candidates={result.analysis.duplicate_matches} />
                </>
              ) : (
                <Alert tone="warning" title="The automated analysis could not be produced">
                  Your complaint has been recorded, but the AI triage agents did not return a result
                  this time. It will be triaged manually by an officer.
                </Alert>
              )}
            </>
          ) : (
            <form className="space-y-6" onSubmit={handleSubmit} noValidate>
              {error ? (
                <Alert tone="danger" role="alert">
                  {error instanceof ApiError ? error.userMessage : error.message}
                </Alert>
              ) : null}

              <Card>
                <CardHeader>
                  <CardTitle>Your complaint</CardTitle>
                  <CardDescription>
                    Text in English, Tamil, or Tamil typed in Latin script is accepted.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div className="space-y-1.5">
                    <Label htmlFor="text">
                      What is the problem? <span className="text-[var(--destructive)]">*</span>
                    </Label>
                    <Textarea
                      id="text"
                      value={text}
                      rows={7}
                      maxLength={5000}
                      placeholder="Describe what you have seen and where."
                      invalid={Boolean(clientError || fieldErrors.text)}
                      onChange={(event) => setText(event.target.value)}
                      required
                    />
                    <div className="flex items-start justify-between gap-3">
                      {clientError ? (
                        <FieldMessage tone="error">{clientError}</FieldMessage>
                      ) : fieldErrors.text ? (
                        <FieldMessage tone="error">{fieldErrors.text}</FieldMessage>
                      ) : (
                        <FieldMessage>Be specific: what, where and since when.</FieldMessage>
                      )}
                      <span className="shrink-0 text-xs tabular-nums text-[var(--muted-foreground)]">
                        {text.length}/5000
                      </span>
                    </div>
                  </div>

                  <div className="grid gap-5 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="language">Language</Label>
                      <Select
                        id="language"
                        value={language}
                        invalid={Boolean(fieldErrors.language)}
                        onChange={(event) =>
                          setLanguage(event.target.value as ComplaintLanguage | '')
                        }
                      >
                        <option value="">Detect automatically</option>
                        {COMPLAINT_LANGUAGES.map((code) => (
                          <option key={code} value={code}>
                            {LANGUAGE_LABELS[code]}
                          </option>
                        ))}
                      </Select>
                      <FieldMessage>Leave this on detection unless you want to be explicit.</FieldMessage>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="category">Category</Label>
                      <Select
                        id="category"
                        value={category}
                        invalid={Boolean(fieldErrors.category)}
                        onChange={(event) =>
                          setCategory(event.target.value as ComplaintCategory | '')
                        }
                      >
                        <option value="">Let the AI classify</option>
                        {COMPLAINT_CATEGORIES.map((code) => (
                          <option key={code} value={code}>
                            {CATEGORY_LABELS[code]}
                          </option>
                        ))}
                      </Select>
                      <FieldMessage>Optional. The AI suggests a category from your text.</FieldMessage>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="location">Location</Label>
                      <Input
                        id="location"
                        value={locationText}
                        placeholder="e.g. Near the bus stand on Main Road"
                        invalid={Boolean(fieldErrors.location_text)}
                        onChange={(event) => setLocationText(event.target.value)}
                      />
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="ward">Ward or zone</Label>
                      <Input
                        id="ward"
                        value={ward}
                        placeholder="e.g. Ward 12"
                        invalid={Boolean(fieldErrors.ward)}
                        onChange={(event) => setWard(event.target.value)}
                      />
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Pin the location (optional)</CardTitle>
                  <CardDescription>
                    Click the map to drop a pin. Coordinates help the officer find the exact spot.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <LocationPicker value={coords} onChange={setCoords} disabled={submitting} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Photos (optional)</CardTitle>
                  <CardDescription>
                    Attach up to five images. They are analysed for relevance, but the officer makes
                    the final call.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <EvidenceUploader photos={photos} onChange={setPhotos} disabled={submitting} />
                  {fieldErrors.evidence ? (
                    <FieldMessage tone="error" className="mt-2">
                      {fieldErrors.evidence}
                    </FieldMessage>
                  ) : null}
                </CardContent>
              </Card>

              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" disabled={submitting}>
                  <Send className="size-4" />
                  {submitting ? 'Submitting…' : 'Submit complaint'}
                </Button>
                <p className="text-xs text-[var(--muted-foreground)]">
                  On submission the AI triage runs and you will see a reference number.
                </p>
              </div>
            </form>
          )}
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2 text-[var(--primary)]">
                <ShieldCheck className="size-4" />
                <CardTitle>How your complaint is handled</CardTitle>
              </div>
            </CardHeader>
            <CardContent className="space-y-4 text-sm text-[var(--muted-foreground)]">
              <ol className="space-y-3">
                <li className="flex gap-3">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[var(--primary-subtle)] text-xs font-semibold text-[var(--primary)]">
                    1
                  </span>
                  <span>
                    <strong className="text-[var(--foreground)]">You submit.</strong> Your words are
                    stored exactly as you wrote them.
                  </span>
                </li>
                <li className="flex gap-3">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[var(--primary-subtle)] text-xs font-semibold text-[var(--primary)]">
                    2
                  </span>
                  <span>
                    <strong className="text-[var(--foreground)]">The AI recommends.</strong> Agents
                    suggest a category, priority, department and resolution.
                  </span>
                </li>
                <li className="flex gap-3">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[var(--primary-subtle)] text-xs font-semibold text-[var(--primary)]">
                    3
                  </span>
                  <span>
                    <strong className="text-[var(--foreground)]">An officer decides.</strong> A human
                    reviews the suggestion and records the official outcome.
                  </span>
                </li>
              </ol>
              <Alert tone="warning">
                <span className="flex items-start gap-2">
                  <Sparkles className="mt-0.5 size-3.5 shrink-0" />
                  The AI only recommends. A human officer makes every official decision.
                </span>
              </Alert>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  )
}
