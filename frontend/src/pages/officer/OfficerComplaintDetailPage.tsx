/**
 * A single complaint, as the officer reviewing it sees it.
 *
 * Every panel on this page belongs to one of exactly two categories: agent
 * output, which is advisory and marked as such, and recorded state, which is
 * what the database actually holds. The only controls that change anything are
 * the duplicate-link and routing decisions, and each of those goes through a
 * confirmation step that writes a reason to the audit trail.
 */

import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, decisionApi, officerApi } from '@/api'
import { AuditTrailPanel } from '@/components/analysis/AuditTrailPanel'
import { ClassificationPanel } from '@/components/analysis/ClassificationPanel'
import { DuplicateReviewPanel } from '@/components/analysis/DuplicateReviewPanel'
import { EvidencePanel } from '@/components/analysis/EvidencePanel'
import { IntakePanel } from '@/components/analysis/IntakePanel'
import { ResolutionPanel } from '@/components/analysis/ResolutionPanel'
import { RoutingApprovalPanel } from '@/components/analysis/RoutingApprovalPanel'
import { AdvisoryNotice } from '@/components/common/AdvisoryNotice'
import { PageHeader } from '@/components/common/PageHeader'
import {
  CategoryBadge,
  LanguageBadge,
  PriorityBadge,
  StatusBadge,
} from '@/components/common/badges'
import { DescriptionItem, DescriptionList, Value } from '@/components/common/DescriptionList'
import { ErrorState, LoadingState } from '@/components/common/states'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuth } from '@/auth/AuthContext'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'
import { formatDateTime, formatRelative } from '@/lib/format'
import { OFFICER_ROLES } from '@/lib/routes'
import type { DuplicateCandidate, ReviewDecision } from '@/types'

type TabValue = 'analysis' | 'duplicates' | 'evidence' | 'routing' | 'resolution' | 'audit'

export default function OfficerComplaintDetailPage() {
  useDocumentTitle('Complaint review')
  const { complaintId } = useParams()
  const { user } = useAuth()
  const [tab, setTab] = useState<TabValue>('analysis')

  const detail = useAsyncData(
    () =>
      complaintId
        ? officerApi.complaintDetail(complaintId)
        : Promise.reject(new Error('No complaint id was provided.')),
    [complaintId],
  )

  const duplicates = useAsyncData(
    () =>
      complaintId
        ? officerApi.duplicateMatches(complaintId)
        : Promise.reject(new Error('No complaint id was provided.')),
    [complaintId],
  )

  // An officer decision changes both the record and the ranked match list, so
  // both are re-fetched rather than patched in the browser.
  const refresh = () => {
    detail.reload()
    duplicates.reload()
  }

  const canDecide = user !== null && OFFICER_ROLES.includes(user.role)

  if (detail.isLoading) return <LoadingState label="Loading the complaint" />

  if (detail.error) {
    if (detail.error instanceof ApiError && detail.error.kind === 'not_found') {
      return (
        <div className="space-y-4">
          <ErrorState error={detail.error} title="That complaint could not be found" />
          <Button variant="outline" asChild>
            <Link to="/officer/complaints">
              <ArrowLeft className="size-4" /> Back to complaints
            </Link>
          </Button>
        </div>
      )
    }
    return <ErrorState error={detail.error} onRetry={detail.reload} />
  }

  if (!detail.data) return null

  const {
    complaint,
    citizen_account,
    original_text,
    normalized_text,
    intake,
    classification,
    recommended_department,
    resolution,
    evidence,
    assignments,
    status_history,
    agent_steps,
    decision_endpoints,
    suggestion_notice,
    provenance,
  } = detail.data

  // The assignment an officer approved, if any - distinct from the AI proposal.
  const approvedAssignment = assignments.find((item) => item.approval_status === 'approved') ?? null
  const approvedDepartment = approvedAssignment
    ? { code: approvedAssignment.department_code, name: approvedAssignment.department_name }
    : null

  const decideDuplicate = async (
    candidate: DuplicateCandidate,
    payload: { decision: ReviewDecision; reason: string; notes: string },
  ) => {
    if (!complaintId || candidate.complaint_id === null) return
    await decisionApi.reviewDuplicate(
      complaintId,
      {
        candidate_id: candidate.complaint_id,
        decision: payload.decision,
        reason: payload.reason.trim() || null,
        notes: payload.notes.trim() || null,
      },
      decision_endpoints,
    )
    refresh()
  }

  const decideRouting = async (payload: {
    decision: ReviewDecision
    reason: string
    notes: string
  }) => {
    if (!complaintId) return
    await decisionApi.decideRouting(
      complaintId,
      {
        decision: payload.decision,
        reason: payload.reason.trim() || null,
        notes: payload.notes.trim() || null,
      },
      decision_endpoints,
    )
    refresh()
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/officer/complaints"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="size-4" /> Back to complaints
        </Link>
      </div>

      <PageHeader
        eyebrow="Complaint review"
        title={complaint.reference_id}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={complaint.status} />
            <PriorityBadge priority={complaint.priority} />
            <CategoryBadge category={complaint.category} />
            <LanguageBadge language={complaint.language} />
            {complaint.sla_breached ? <Badge tone="danger">SLA breached</Badge> : null}
            {complaint.pending_human_review ? (
              <Badge tone="warning">Awaiting a decision</Badge>
            ) : null}
          </span>
        }
      />

      <AdvisoryNotice notice={suggestion_notice} provenance={provenance} />

      <Card>
        <CardHeader>
          <CardTitle>Recorded details</CardTitle>
          <CardDescription>
            The facts the database holds about this complaint, as distinct from any agent output.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DescriptionList columns={3}>
            <DescriptionItem label="Status">
              <StatusBadge status={complaint.status} />
            </DescriptionItem>
            <DescriptionItem label="Submitting citizen">
              <Value>{citizen_account.display_name ?? citizen_account.user_id}</Value>
              {citizen_account.is_synthetic ? (
                <span className="ml-2 text-xs text-[var(--muted-foreground)]">(prototype)</span>
              ) : null}
            </DescriptionItem>
            <DescriptionItem label="Preferred language">
              <Value>
                {citizen_account.preferred_language
                  ? citizen_account.preferred_language.toUpperCase()
                  : null}
              </Value>
            </DescriptionItem>
            <DescriptionItem label="Ward / zone">
              <Value>{complaint.ward}</Value>
            </DescriptionItem>
            <DescriptionItem label="Location">
              <Value>{complaint.location_text}</Value>
            </DescriptionItem>
            <DescriptionItem label="Evidence items">
              {complaint.evidence_count}
            </DescriptionItem>
            <DescriptionItem label="Reported">
              {formatDateTime(complaint.reported_at ?? complaint.submitted_at)}
            </DescriptionItem>
            <DescriptionItem label="SLA due">
              {complaint.sla_due_at ? formatDateTime(complaint.sla_due_at) : 'Not set'}
            </DescriptionItem>
            <DescriptionItem label="Duplicate of">
              {complaint.is_duplicate ? (
                <Value>{complaint.duplicate_of_reference}</Value>
              ) : (
                'Not flagged as a duplicate'
              )}
            </DescriptionItem>
          </DescriptionList>
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={(value) => setTab(value as TabValue)} id="officer-detail">
        <TabsList>
          <TabsTrigger value="analysis">AI analysis</TabsTrigger>
          <TabsTrigger value="duplicates">Duplicates</TabsTrigger>
          <TabsTrigger value="evidence">Evidence</TabsTrigger>
          <TabsTrigger value="routing">Routing</TabsTrigger>
          <TabsTrigger value="resolution">Resolution</TabsTrigger>
          <TabsTrigger value="audit">History</TabsTrigger>
        </TabsList>

        <TabsContent value="analysis" className="space-y-5">
          <IntakePanel intake={intake} originalText={original_text} />

          {normalized_text && normalized_text !== original_text ? (
            <Alert tone="info" title="Normalised text on file">
              {normalized_text}
            </Alert>
          ) : null}

          <ClassificationPanel
            classification={classification}
            approvedDepartment={approvedDepartment}
          />
        </TabsContent>

        <TabsContent value="duplicates">
          <DuplicateReviewPanel
            page={duplicates.data}
            isLoading={duplicates.isLoading}
            error={duplicates.error}
            onRetry={duplicates.reload}
            onDecide={decideDuplicate}
          />
        </TabsContent>

        <TabsContent value="evidence">
          <EvidencePanel evidence={evidence} />
        </TabsContent>

        <TabsContent value="routing">
          <RoutingApprovalPanel
            recommendation={recommended_department}
            assignments={assignments}
            approvedDepartment={approvedDepartment}
            onDecide={decideRouting}
            canDecide={canDecide}
          />
        </TabsContent>

        <TabsContent value="resolution">
          <ResolutionPanel resolution={resolution} />
        </TabsContent>

        <TabsContent value="audit" className="space-y-5">
          <AuditTrailPanel
            statusHistory={status_history}
            assignments={assignments}
            agentSteps={agent_steps}
            currentStatus={complaint.status}
          />
          <p className="text-xs text-[var(--muted-foreground)]">
            Last updated {formatRelative(complaint.submitted_at ?? complaint.reported_at)}.
          </p>
        </TabsContent>
      </Tabs>
    </div>
  )
}
