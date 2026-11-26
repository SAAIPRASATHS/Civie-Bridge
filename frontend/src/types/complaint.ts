/** Citizen-facing complaint payloads, mirroring `app/api/citizens.py`. */

import type {
  ComplaintCategory,
  ComplaintLanguage,
  ComplaintPriority,
  ComplaintStatus,
  Page,
  WorkflowOutcome,
} from './common'
import type { WorkflowRunResult } from './analysis'

export interface CitizenComplaintSummary {
  complaint_id: string
  reference_id: string
  status: ComplaintStatus
  category: ComplaintCategory | null
  priority: ComplaintPriority | null
  language: ComplaintLanguage
  location_text: string | null
  ward: string | null
  submitted_at: string | null
  is_duplicate: boolean
  duplicate_of_reference: string | null
  needs_clarification: boolean
  clarification_question: string | null
}

export interface CitizenEvidenceInput {
  file_name: string
  content_base64: string
  content_type?: string | null
}

export interface ComplaintSubmissionRequest {
  text: string
  language?: ComplaintLanguage | null
  category?: ComplaintCategory | null
  location_text?: string | null
  ward?: string | null
  latitude?: number | null
  longitude?: number | null
  evidence?: CitizenEvidenceInput[]
  request_id?: string | null
}

export interface ComplaintSubmissionResponse {
  complaint: CitizenComplaintSummary
  original_text: string
  analysis: WorkflowRunResult | null
  analysis_outcome: WorkflowOutcome | string
  replayed: boolean
  suggestion_notice: string
  provenance: string
  message: string
}

export type CitizenComplaintPage = Page<CitizenComplaintSummary>

export interface CitizenStatusEvent {
  from_status: ComplaintStatus | null
  to_status: ComplaintStatus
  changed_at: string | null
  reason: string | null
  by_agent: string | null
}

export interface CitizenEvidenceItem {
  evidence_id: string
  file_name: string
  verification_status: string
  requires_manual_review: boolean
  uploaded_at: string | null
}

export interface CitizenComplaintDetail {
  complaint: CitizenComplaintSummary
  original_text: string
  status_history: CitizenStatusEvent[]
  evidence: CitizenEvidenceItem[]
  suggestion_notice: string
}
