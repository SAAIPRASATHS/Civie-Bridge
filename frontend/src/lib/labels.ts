/**
 * Display labels and colour tones for the controlled vocabularies.
 *
 * The backend stores machine values (`waste_management`, `under_review`); every
 * human-facing string is produced here so a status reads identically on the
 * citizen timeline, the officer queue and the analytics legend.
 */

import type {
  AgentName,
  AssignmentApprovalStatus,
  ComplaintCategory,
  ComplaintLanguage,
  ComplaintPriority,
  ComplaintStatus,
  EvidenceVerificationStatus,
  UserRole,
  WorkflowOutcome,
} from '@/types'

/** The semantic tones a badge or dot can take. */
export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'primary'

const titleCase = (value: string): string =>
  value
    .split(/[_-]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')

export const STATUS_TONES: Record<ComplaintStatus, Tone> = {
  submitted: 'info',
  awaiting_clarification: 'warning',
  under_review: 'info',
  awaiting_approval: 'warning',
  assigned: 'primary',
  in_progress: 'primary',
  resolved: 'success',
  closed: 'neutral',
  rejected: 'danger',
  duplicate: 'neutral',
  escalated: 'danger',
}

export function statusLabel(status: ComplaintStatus): string {
  return titleCase(status)
}

export function statusTone(status: ComplaintStatus): Tone {
  return STATUS_TONES[status] ?? 'neutral'
}

export const PRIORITY_TONES: Record<ComplaintPriority, Tone> = {
  low: 'neutral',
  medium: 'info',
  high: 'warning',
  critical: 'danger',
}

export function priorityLabel(priority: ComplaintPriority): string {
  return titleCase(priority)
}

export function priorityTone(priority: ComplaintPriority): Tone {
  return PRIORITY_TONES[priority] ?? 'neutral'
}

export const CATEGORY_LABELS: Record<ComplaintCategory, string> = {
  waste_management: 'Waste management',
  road_maintenance: 'Road maintenance',
  water_supply: 'Water supply',
  street_lighting: 'Street lighting',
}

export function categoryLabel(category: ComplaintCategory | null | undefined): string {
  if (!category) return 'Not classified'
  return CATEGORY_LABELS[category] ?? titleCase(category)
}

export const LANGUAGE_LABELS: Record<ComplaintLanguage, string> = {
  en: 'English',
  ta: 'Tamil',
  'ta-latn': 'Tamil (Latin script)',
}

export function languageLabel(language: ComplaintLanguage | null | undefined): string {
  if (!language) return '-'
  return LANGUAGE_LABELS[language] ?? language
}

export const ROLE_LABELS: Record<UserRole, string> = {
  citizen: 'Citizen',
  officer: 'Officer',
  admin: 'Administrator',
}

export const EVIDENCE_STATUS_LABELS: Record<EvidenceVerificationStatus, string> = {
  pending: 'Pending',
  verified_relevant: 'Verified relevant',
  uncertain: 'Uncertain',
  not_relevant: 'Not relevant',
  manual_review_required: 'Manual review required',
}

export const EVIDENCE_STATUS_TONES: Record<EvidenceVerificationStatus, Tone> = {
  pending: 'neutral',
  verified_relevant: 'success',
  uncertain: 'warning',
  not_relevant: 'danger',
  manual_review_required: 'warning',
}

export const ASSIGNMENT_STATUS_LABELS: Record<AssignmentApprovalStatus, string> = {
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
  reassigned: 'Reassigned',
}

export const ASSIGNMENT_STATUS_TONES: Record<AssignmentApprovalStatus, Tone> = {
  pending: 'warning',
  approved: 'success',
  rejected: 'danger',
  reassigned: 'info',
}

export const AGENT_LABELS: Record<AgentName, string> = {
  intake: 'Intake',
  classification: 'Classification',
  duplicate_detection: 'Duplicate detection',
  evidence_verification: 'Evidence verification',
  department_routing: 'Department routing',
  resolution_recommendation: 'Resolution recommendation',
  sla_escalation: 'SLA escalation',
}

export const OUTCOME_LABELS: Record<WorkflowOutcome, string> = {
  completed: 'Completed',
  awaiting_human_review: 'Awaiting human review',
  degraded: 'Degraded (deterministic fallback)',
  failed: 'Failed',
}

export const OUTCOME_TONES: Record<WorkflowOutcome, Tone> = {
  completed: 'success',
  awaiting_human_review: 'warning',
  degraded: 'warning',
  failed: 'danger',
}

/** Human-readable label for a free-form snake_case key from a payload. */
export function humanize(value: string): string {
  return titleCase(value)
}
