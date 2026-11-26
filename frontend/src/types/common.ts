/**
 * Controlled vocabularies mirrored from `app/models/enums.py`.
 *
 * These unions are the frontend's copy of the backend's stored values. They are
 * deliberately literal (not `string`) so a typo in a filter or a badge lookup is
 * a compile error rather than a silently empty list. If the backend adds a
 * member, this file must be updated in the same commit.
 */

export const USER_ROLES = ['citizen', 'officer', 'admin'] as const
export type UserRole = (typeof USER_ROLES)[number]

export const COMPLAINT_CATEGORIES = [
  'waste_management',
  'road_maintenance',
  'water_supply',
  'street_lighting',
] as const
export type ComplaintCategory = (typeof COMPLAINT_CATEGORIES)[number]

export const COMPLAINT_PRIORITIES = ['low', 'medium', 'high', 'critical'] as const
export type ComplaintPriority = (typeof COMPLAINT_PRIORITIES)[number]

export const COMPLAINT_STATUSES = [
  'submitted',
  'awaiting_clarification',
  'under_review',
  'awaiting_approval',
  'assigned',
  'in_progress',
  'resolved',
  'closed',
  'rejected',
  'duplicate',
  'escalated',
] as const
export type ComplaintStatus = (typeof COMPLAINT_STATUSES)[number]

export const COMPLAINT_LANGUAGES = ['en', 'ta', 'ta-latn'] as const
export type ComplaintLanguage = (typeof COMPLAINT_LANGUAGES)[number]

export const EVIDENCE_VERIFICATION_STATUSES = [
  'pending',
  'verified_relevant',
  'uncertain',
  'not_relevant',
  'manual_review_required',
] as const
export type EvidenceVerificationStatus = (typeof EVIDENCE_VERIFICATION_STATUSES)[number]

export const ASSIGNMENT_APPROVAL_STATUSES = [
  'pending',
  'approved',
  'rejected',
  'reassigned',
] as const
export type AssignmentApprovalStatus = (typeof ASSIGNMENT_APPROVAL_STATUSES)[number]

export const AGENT_NAMES = [
  'intake',
  'classification',
  'duplicate_detection',
  'evidence_verification',
  'department_routing',
  'resolution_recommendation',
  'sla_escalation',
] as const
export type AgentName = (typeof AGENT_NAMES)[number]

export const AGENT_EXECUTION_STATUSES = [
  'started',
  'succeeded',
  'failed',
  'skipped',
  'awaiting_human',
] as const
export type AgentExecutionStatus = (typeof AGENT_EXECUTION_STATUSES)[number]

export const WORKFLOW_OUTCOMES = [
  'completed',
  'awaiting_human_review',
  'degraded',
  'failed',
] as const
export type WorkflowOutcome = (typeof WORKFLOW_OUTCOMES)[number]

/** A page envelope shared by the list endpoints. */
export interface Page<T> {
  total: number
  limit: number
  offset: number
  returned: number
  items: T[]
}
