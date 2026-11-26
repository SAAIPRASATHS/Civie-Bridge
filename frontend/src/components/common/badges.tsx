/**
 * Status, priority and category badges.
 *
 * These are the single place a stored enum becomes a coloured label, so the
 * same complaint reads identically on every screen. `StatusBadge` also carries
 * a title explaining that the value is the official recorded status.
 */

import { Badge } from '@/components/ui/badge'
import {
  ASSIGNMENT_STATUS_LABELS,
  ASSIGNMENT_STATUS_TONES,
  EVIDENCE_STATUS_LABELS,
  EVIDENCE_STATUS_TONES,
  LANGUAGE_LABELS,
  ROLE_LABELS,
  categoryLabel,
  priorityLabel,
  priorityTone,
  statusLabel,
  statusTone,
} from '@/lib/labels'
import type {
  AssignmentApprovalStatus,
  ComplaintCategory,
  ComplaintLanguage,
  ComplaintPriority,
  ComplaintStatus,
  EvidenceVerificationStatus,
  UserRole,
} from '@/types'

export function StatusBadge({
  status,
  className,
}: {
  status: ComplaintStatus
  className?: string
}) {
  return (
    <Badge tone={statusTone(status)} className={className} title="Official recorded status">
      {statusLabel(status)}
    </Badge>
  )
}

export function PriorityBadge({
  priority,
  className,
}: {
  priority: ComplaintPriority | null
  className?: string
}) {
  if (!priority) {
    return (
      <Badge tone="neutral" className={className} title="No priority has been recorded">
        Unassessed
      </Badge>
    )
  }
  return (
    <Badge tone={priorityTone(priority)} className={className} title="AI-assessed priority">
      {priorityLabel(priority)}
    </Badge>
  )
}

export function CategoryBadge({
  category,
  className,
}: {
  category: ComplaintCategory | null
  className?: string
}) {
  if (!category) {
    return (
      <Badge tone="warning" className={className} title="Needs manual triage">
        Unclassified
      </Badge>
    )
  }
  return (
    <Badge tone="info" className={className} title="AI-classified category">
      {categoryLabel(category)}
    </Badge>
  )
}

export function LanguageBadge({
  language,
  className,
}: {
  language: ComplaintLanguage
  className?: string
}) {
  return (
    <Badge tone="neutral" className={className}>
      {LANGUAGE_LABELS[language]}
    </Badge>
  )
}

export function EvidenceStatusBadge({
  status,
  className,
}: {
  status: EvidenceVerificationStatus
  className?: string
}) {
  return (
    <Badge tone={EVIDENCE_STATUS_TONES[status]} className={className}>
      {EVIDENCE_STATUS_LABELS[status]}
    </Badge>
  )
}

export function AssignmentStatusBadge({
  status,
  className,
}: {
  status: AssignmentApprovalStatus
  className?: string
}) {
  return (
    <Badge tone={ASSIGNMENT_STATUS_TONES[status]} className={className}>
      {ASSIGNMENT_STATUS_LABELS[status]}
    </Badge>
  )
}

export function RoleBadge({ role, className }: { role: UserRole; className?: string }) {
  const tone = role === 'admin' ? 'danger' : role === 'officer' ? 'primary' : 'neutral'
  return (
    <Badge tone={tone} className={className}>
      {ROLE_LABELS[role]}
    </Badge>
  )
}

/** A compact "AI suggestion" marker, kept visually distinct from a decision. */
export function AiSuggestionBadge({ className }: { className?: string }) {
  return (
    <Badge tone="warning" className={className} title="Advisory agent output, not a decision">
      AI suggestion
    </Badge>
  )
}

/** A marker for a value a human has officially approved. */
export function ApprovedBadge({ className }: { className?: string }) {
  return (
    <Badge tone="success" className={className} title="Confirmed by an officer">
      Officer-approved
    </Badge>
  )
}
