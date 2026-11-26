/**
 * Officer decision payloads, mirroring `app/api/duplicates.py` and
 * `app/api/routing.py`.
 *
 * These are the only endpoints that change a complaint's official state. The
 * request shapes are kept exact - `extra="forbid"` on the backend means an
 * unexpected field is a 422, not a silent no-op.
 */

import type { AssignmentApprovalStatus, ComplaintStatus } from './common'
import type { DuplicateCandidate } from './analysis'

export type ReviewDecision = 'approve' | 'reject'

/**
 * A page of ranked potential duplicate matches for a stored complaint,
 * mirroring `DuplicateMatchesPage` in `app/api/duplicates.py`.
 */
export interface DuplicateMatchesPage {
  complaint_id: string
  reference_id: string
  subject_text: string

  candidates_considered: number
  total_matches: number
  limit: number
  offset: number
  returned: number
  items: DuplicateCandidate[]

  best_match: DuplicateCandidate | null
  likely_duplicate_found: boolean
  threshold: number
  text_only_threshold: number
  geographic_radius_m: number
  weights: Record<string, number>

  linking_requires_human_approval: boolean
  reasoning: string
}

export interface DuplicateReviewRequest {
  candidate_id: string
  decision: ReviewDecision
  reason?: string | null
  notes?: string | null
}

export interface DuplicateReviewResult {
  complaint_id: string
  reference_id: string
  candidate_id: string
  candidate_reference_id: string

  decision: ReviewDecision
  linked: boolean
  match_score: number
  text_similarity: number
  explanation: string

  status: ComplaintStatus
  audit_recorded: boolean
  audit_record_id: string | null
  linking_requires_human_approval: boolean
  message: string
}

export interface RoutingDecisionRequest {
  decision: ReviewDecision
  officer_id?: string | null
  reason?: string | null
  notes?: string | null
}

export interface RoutingDecisionResult {
  complaint_id: string
  reference_id: string
  decision: ReviewDecision
  approval_status: AssignmentApprovalStatus
  department_code: string | null
  department_name: string | null

  assignment_id: string
  assigned: boolean
  status: ComplaintStatus
  recommendation_confidence: number

  audit_recorded: boolean
  audit_record_id: string | null
  requires_human_approval: boolean
  message: string
}
