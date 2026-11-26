/**
 * A single complaint, as the citizen sees it.
 *
 * Only what the citizen is entitled to is shown: their own words, the official
 * status history and the evidence they attached. The officer-facing reasoning
 * and evidence analysis are intentionally absent, because the citizen endpoints
 * do not expose them and presenting them would imply a decision that has not
 * been recorded.
 */

import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, citizenApi } from '@/api'
import { CategoryBadge, LanguageBadge, PriorityBadge, StatusBadge } from '@/components/common/badges'
import { AdvisoryNotice } from '@/components/common/AdvisoryNotice'
import { ComplaintTimeline } from '@/components/citizen/ComplaintTimeline'
import { DescriptionItem, DescriptionList, Value } from '@/components/common/DescriptionList'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/common/states'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { formatDateTime } from '@/lib/format'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'
import { humanize } from '@/lib/labels'

export default function ComplaintDetailPage() {
  useDocumentTitle('Complaint detail')
  const { referenceId } = useParams()

  const { data, error, isLoading, reload } = useAsyncData(
    () =>
      referenceId
        ? citizenApi.getMine(referenceId)
        : Promise.reject(new Error('No complaint reference was provided.')),
    [referenceId],
  )

  if (isLoading) return <LoadingState label="Loading your complaint" />

  if (error) {
    if (error instanceof ApiError && error.kind === 'not_found') {
      return (
        <div className="space-y-4">
          <ErrorState error={error} title="That complaint could not be found" />
          <p className="text-sm text-[var(--muted-foreground)]">
            Citizens can only view complaints submitted from their own account. Check the reference
            number, or return to your complaint list.
          </p>
          <Button variant="outline" asChild>
            <Link to="/app/complaints">
              <ArrowLeft className="size-4" /> Back to my complaints
            </Link>
          </Button>
        </div>
      )
    }
    return <ErrorState error={error} onRetry={reload} />
  }

  if (!data) return null

  const { complaint, original_text, status_history, evidence, suggestion_notice } = data

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/app/complaints"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="size-4" /> Back to my complaints
        </Link>
      </div>

      <PageHeader
        eyebrow="Complaint"
        title={complaint.reference_id}
        description="The official record of this complaint, including your original words and every recorded status change."
        actions={<StatusBadge status={complaint.status} />}
      />

      {complaint.needs_clarification ? (
        <Alert tone="warning" title="An officer needs more detail">
          {complaint.clarification_question ??
            'The officer reviewing this complaint has asked a clarifying question.'}
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Recorded details</CardTitle>
          <CardDescription>The facts stored against this complaint.</CardDescription>
        </CardHeader>
        <CardContent>
          <DescriptionList columns={2}>
            <DescriptionItem label="Status">
              <StatusBadge status={complaint.status} />
            </DescriptionItem>
            <DescriptionItem label="Category">
              <CategoryBadge category={complaint.category} />
            </DescriptionItem>
            <DescriptionItem label="Priority">
              <PriorityBadge priority={complaint.priority} />
            </DescriptionItem>
            <DescriptionItem label="Language">
              <LanguageBadge language={complaint.language} />
            </DescriptionItem>
            <DescriptionItem label="Ward">
              <Value>{complaint.ward}</Value>
            </DescriptionItem>
            <DescriptionItem label="Location">
              <Value>{complaint.location_text}</Value>
            </DescriptionItem>
            <DescriptionItem label="Submitted">
              {formatDateTime(complaint.submitted_at)}
            </DescriptionItem>
            {complaint.is_duplicate ? (
              <DescriptionItem label="Duplicate of">
                <Value>{complaint.duplicate_of_reference}</Value>
              </DescriptionItem>
            ) : null}
          </DescriptionList>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your words (verbatim)</CardTitle>
          <CardDescription>The text exactly as you submitted it.</CardDescription>
        </CardHeader>
        <CardContent>
          <blockquote className="whitespace-pre-wrap rounded-md border-l-4 border-[var(--primary)] bg-[var(--primary-subtle)] px-4 py-3 text-sm leading-relaxed">
            {original_text}
          </blockquote>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Status timeline</CardTitle>
          <CardDescription>Every recorded change to this complaint&apos;s status.</CardDescription>
        </CardHeader>
        <CardContent>
          <ComplaintTimeline events={status_history} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Photos you attached</CardTitle>
          <CardDescription>Images you sent with the complaint.</CardDescription>
        </CardHeader>
        <CardContent>
          {evidence.length === 0 ? (
            <EmptyState
              title="No photos attached"
              description="This complaint was submitted without photographs."
            />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {evidence.map((item) => (
                <li
                  key={item.evidence_id}
                  className="flex flex-wrap items-center justify-between gap-2 py-3 first:pt-0 last:pb-0"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium" title={item.file_name}>
                      {item.file_name}
                    </p>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      Uploaded {formatDateTime(item.uploaded_at)}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="neutral">{humanize(item.verification_status)}</Badge>
                    {item.requires_manual_review ? (
                      <Badge tone="warning">Needs manual review</Badge>
                    ) : (
                      <Badge tone="success">No manual review needed</Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <AdvisoryNotice
        notice={suggestion_notice}
        title="About the automated analysis"
      />
    </div>
  )
}
