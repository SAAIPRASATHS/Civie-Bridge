/**
 * AI output shapes, mirroring `app/schemas/agent.py` and `app/schemas/workflow.py`.
 *
 * Every field here is advisory agent output. Nothing in this file represents an
 * approved decision, and the optional fields are genuinely optional: the agents
 * are allowed to return "not determined" rather than guess, so a `null` category
 * or a missing priority is meaningful, not missing data.
 */

import type {
  AgentExecutionStatus,
  ComplaintCategory,
  ComplaintLanguage,
  ComplaintPriority,
  EvidenceVerificationStatus,
  WorkflowOutcome,
} from './common'

export interface IntakeResult {
  original_text: string
  language: ComplaintLanguage
  language_confidence: number
  language_reason: string
  normalized_description: string
  problem_type: string | null
  duration_days: number | null
  duration_text: string | null
  location_text: string | null
  ward: string | null
  landmarks: string[]
  missing_information: string[]
  clarification_questions: string[]
  needs_clarification: boolean
  confidence: number
  reasoning: string
  unsupported_details_dropped: string[]
  llm_used: boolean
  degraded: boolean
  latency_ms: number | null
  model_name: string | null
}

export interface ClassificationResult {
  category: ComplaintCategory | null
  subcategory: string | null
  priority: ComplaintPriority
  urgency_score: number
  confidence: number
  reasoning: string
  recommended_next_action: string

  deterministic_priority: ComplaintPriority
  model_priority: ComplaintPriority | null
  escalation_reason: string | null

  department_code: string | null
  department_name: string | null
  guideline_code: string | null
  sla_hours: number | null
  routing_source: string

  matched_signals: string[]
  needs_human_review: boolean
  review_reason: string | null

  llm_used: boolean
  degraded: boolean
  latency_ms: number | null
  model_name: string | null
}

export interface DuplicateCandidate {
  reference_id: string
  text: string
  category: ComplaintCategory | null
  ward: string | null
  latitude: number | null
  longitude: number | null

  /**
   * The stored complaint this candidate is. Set only by the officer-facing
   * duplicate lookup, which is the route that feeds the approve/reject review;
   * the review endpoint addresses the candidate by this id, not by reference.
   */
  complaint_id: string | null

  text_similarity: number
  geographic_distance_m: number | null
  geographic_score: number | null
  category_match: boolean | null
  match_score: number
  is_likely_duplicate: boolean
  shared_terms: string[]
  reasons: string[]
  model_adjudication: string | null
}

export interface DuplicateDetectionResult {
  subject_reference_id: string | null
  subject_text: string
  candidates_considered: number
  matches: DuplicateCandidate[]
  best_match: DuplicateCandidate | null
  likely_duplicate_found: boolean

  threshold: number
  text_only_threshold: number
  geographic_radius_m: number
  weights: Record<string, number>

  needs_human_review: boolean
  review_reason: string | null
  linking_requires_human_approval: boolean

  confidence: number
  reasoning: string
  llm_used: boolean
  degraded: boolean
  latency_ms: number | null
  model_name: string | null
}

export interface ObservableFinding {
  kind: string
  statement: string
  measured_value: string | null
  confidence: number
}

export interface AssumptionFinding {
  statement: string
  basis: string
  source: string
  confidence: number
}

export interface EvidenceAnalysisResult {
  file_name: string | null
  content_digest: string | null
  category: ComplaintCategory | null

  image_analyzed: boolean
  image_usable: boolean
  width: number | null
  height: number | null

  blur_score: number | null
  brightness: number | null
  contrast: number | null
  dark_pixel_ratio: number | null
  edge_density: number | null

  relevance_score: number
  relevance_ceiling: number
  category_consistency: string

  verification_status: EvidenceVerificationStatus
  requires_manual_review: boolean

  observable_findings: ObservableFinding[]
  assumptions: AssumptionFinding[]
  limitations: string[]
  unsupported_claims_dropped: string[]

  verification_notes: string
  reasoning: string
  recommendation: string
  is_proof_of_validity: boolean

  confidence: number
  llm_used: boolean
  degraded: boolean
  latency_ms: number | null
  model_name: string | null
}

export interface RoutingRecommendation {
  subject_text: string | null
  category: ComplaintCategory | null

  recommended_department_code: string | null
  recommended_department_name: string | null
  routing_source: string
  is_prototype_data: boolean

  confidence: number
  matched_rules: string[]
  missing_information: string[]

  ward: string | null
  location_text: string | null
  has_coordinates: boolean

  requires_human_review: boolean
  review_reason: string | null

  rationale: string
  provenance: string

  llm_used: boolean
  degraded: boolean
  latency_ms: number | null
  model_name: string | null
}

export interface ResolutionRecommendation {
  subject_text: string | null
  category: ComplaintCategory | null

  guideline_code: string | null
  guideline_title: string | null
  department_code: string | null

  sla_hours: number | null
  sla_is_verified: boolean
  sla_disclaimer: string
  sla_note: string

  resolution_steps: string[]
  suggested_next_steps: string[]
  completed_actions: string[]
  escalation_matrix: Record<string, unknown>
  required_evidence: string[]
  missing_evidence: string[]
  citizen_response_template: string | null

  is_prototype_data: boolean
  recommendation_only: boolean
  status_change_applied: boolean
  requires_human_approval: boolean
  no_auto_resolution: boolean

  confidence: number
  requires_human_review: boolean
  review_reason: string | null

  rationale: string
  provenance: string

  llm_used: boolean
  degraded: boolean
  latency_ms: number | null
  model_name: string | null
}

export interface NodeTrace {
  node: string
  status: AgentExecutionStatus
  attempt: number
  duration_ms: number
  skipped: boolean
  skipped_reason: string | null
  error: string | null
  degraded: boolean
}

/** The complete result of one orchestration run, as returned on submission. */
export interface WorkflowRunResult {
  workflow_run_id: string
  request_id: string | null
  outcome: WorkflowOutcome

  complaint_id: string | null
  reference_id: string | null

  intake: IntakeResult | null
  classification: ClassificationResult | null
  duplicate: DuplicateDetectionResult | null
  duplicate_matches: DuplicateCandidate[]
  evidence: EvidenceAnalysisResult[]
  routing: RoutingRecommendation | null
  resolution: ResolutionRecommendation | null

  review_required: boolean
  review_reasons: string[]
  human_review_completed: boolean

  errors: string[]
  degraded: boolean
  degraded_reasons: string[]

  nodes: NodeTrace[]
  node_latencies_ms: Record<string, number>
  total_latency_ms: number
  persisted: boolean

  requires_human_approval: boolean
  automatic_consequential_actions: boolean
  provenance: string
}
