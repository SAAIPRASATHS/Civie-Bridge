/** Officer dashboard payloads, mirroring `app/api/officers.py`. */

import type {
  AgentExecutionStatus,
  AgentName,
  AssignmentApprovalStatus,
  ComplaintCategory,
  ComplaintLanguage,
  ComplaintPriority,
  ComplaintStatus,
  EvidenceVerificationStatus,
  Page,
} from './common'
import type {
  ClassificationResult,
  DuplicateDetectionResult,
  EvidenceAnalysisResult,
  IntakeResult,
  ResolutionRecommendation,
  RoutingRecommendation,
} from './analysis'

export interface OfficerComplaintSummary {
  complaint_id: string
  reference_id: string
  status: ComplaintStatus
  pending_human_review: boolean

  category: ComplaintCategory | null
  priority: ComplaintPriority | null
  language: ComplaintLanguage
  ward: string | null
  location_text: string | null

  recommended_department_code: string | null
  recommended_department_name: string | null
  department_is_approved: boolean

  classification_confidence: number | null
  urgency_score: number | null

  reported_at: string | null
  submitted_at: string | null
  sla_due_at: string | null
  sla_breached: boolean

  is_duplicate: boolean
  duplicate_of_reference: string | null
  duplicate_similarity: number | null
  needs_clarification: boolean
  evidence_count: number
}

export interface OfficerComplaintPage extends Page<OfficerComplaintSummary> {
  suggestion_notice: string
}

export interface OfficerCitizenAccount {
  user_id: string | null
  display_name: string | null
  preferred_language: string | null
  is_synthetic: boolean
}

export interface OfficerEvidenceItem {
  evidence_id: string
  file_name: string
  mime_type: string | null
  file_size_bytes: number | null
  caption: string | null

  verification_status: EvidenceVerificationStatus
  relevance_score: number | null
  requires_manual_review: boolean
  verification_notes: string | null
  agent_explanation: string | null
  analysis_model: string | null
  uploaded_at: string | null

  analysis: EvidenceAnalysisResult | null
}

export interface OfficerAssignmentItem {
  assignment_id: string
  department_code: string | null
  department_name: string | null
  officer_id: string | null

  ai_recommended_department_code: string | null
  ai_confidence: number | null
  ai_explanation: string | null

  approval_status: AssignmentApprovalStatus
  assigned_by_user_id: string | null
  approved_by_user_id: string | null
  approved_at: string | null
  rejection_reason: string | null
  notes: string | null
  created_at: string | null
}

export interface OfficerAgentStep {
  agent_name: AgentName
  step_index: number | null
  status: AgentExecutionStatus
  explanation: string | null
  confidence: number | null
  model_name: string | null
  duration_ms: number | null
  workflow_run_id: string | null
  started_at: string | null
  completed_at: string | null
}

export interface OfficerStatusEvent {
  history_id: string
  from_status: ComplaintStatus | null
  to_status: ComplaintStatus
  changed_by_user_id: string | null
  changed_by_agent: string | null
  reason: string | null
  notes: string | null
  changed_at: string | null
}

/**
 * The routes an officer must call to actually approve or reject a decision.
 * Returned by the backend so the frontend never hardcodes a decision URL.
 */
export interface OfficerDecisionEndpoints {
  duplicate_review?: string
  routing_decision?: string
  routing_recommendation?: string
  duplicate_matches?: string
  [key: string]: string | undefined
}

export interface OfficerComplaintDetail {
  complaint: OfficerComplaintSummary
  citizen_account: OfficerCitizenAccount

  original_text: string
  normalized_text: string | null

  intake: IntakeResult | null
  classification: ClassificationResult | null
  recommended_department: RoutingRecommendation | null
  resolution: ResolutionRecommendation | null
  duplicate_matches: DuplicateDetectionResult | null

  evidence: OfficerEvidenceItem[]
  assignments: OfficerAssignmentItem[]
  status_history: OfficerStatusEvent[]
  agent_steps: OfficerAgentStep[]

  decision_endpoints: OfficerDecisionEndpoints

  suggestion_notice: string
  provenance: string
}

export interface OfficerAuditTrail {
  complaint_id: string
  reference_id: string
  current_status: ComplaintStatus
  status_history: OfficerStatusEvent[]
  assignments: OfficerAssignmentItem[]
  agent_steps: OfficerAgentStep[]
}

export interface DepartmentDirectoryItem {
  department_id: string
  code: string
  name: string
  category: ComplaintCategory
  jurisdiction: string | null
  is_active: boolean
  is_prototype_data: boolean
}

export interface DepartmentDirectoryPage {
  total: number
  items: DepartmentDirectoryItem[]
  provenance: string
}

export interface AnalyticsBucket {
  key: string
  label: string
  count: number
}

export interface AnalyticsTrendPoint {
  period: string
  count: number
}

export interface OfficerAnalytics {
  generated_at: string
  window_days: number
  total_complaints: number
  pending_review: number
  decided: number
  sla_breached: number
  awaiting_triage: number
  unassigned: number
  by_status: AnalyticsBucket[]
  by_category: AnalyticsBucket[]
  by_priority: AnalyticsBucket[]
  by_language: AnalyticsBucket[]
  by_department: AnalyticsBucket[]
  submitted_trend: AnalyticsTrendPoint[]
  resolved_trend: AnalyticsTrendPoint[]
  provenance: string
}

/** Query parameters accepted by `GET /api/officer/complaints`. */
export interface OfficerComplaintFilters {
  status?: ComplaintStatus[]
  priority?: ComplaintPriority
  category?: ComplaintCategory
  department_id?: string
  ward?: string
  language?: ComplaintLanguage
  pending_review_only?: boolean
  search?: string
  limit?: number
  offset?: number
}
