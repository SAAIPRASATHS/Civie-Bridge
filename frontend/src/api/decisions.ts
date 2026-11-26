/**
 * The officer decision service - the only calls that change a complaint's
 * official state.
 *
 * These routes are documented by the backend in
 * `OfficerComplaintDetail.decision_endpoints` (and verified against
 * `app/api/duplicates.py` and `app/api/routing.py`). Where the backend supplies
 * a route, it is used verbatim so the frontend cannot drift from it; otherwise
 * the verified path is constructed from the complaint id.
 */

import { apiClient } from './client'
import type {
  DuplicateReviewRequest,
  DuplicateReviewResult,
  OfficerDecisionEndpoints,
  RoutingDecisionRequest,
  RoutingDecisionResult,
} from '@/types'

/**
 * Strip the API mount prefix from a server-provided path.
 *
 * The instance's `baseURL` is already `/api`, and the backend reports paths
 * including it (`/api/complaints/{id}/...`), so the prefix is removed to avoid
 * producing `/api/api/...`.
 */
function servicePath(endpoint: string | undefined, fallback: string): string {
  if (!endpoint) return fallback
  return endpoint.startsWith('/api/') ? endpoint.slice('/api'.length) : endpoint
}

export const decisionApi = {
  /** Approve or reject one proposed duplicate link. */
  async reviewDuplicate(
    complaintId: string,
    payload: DuplicateReviewRequest,
    endpoints: OfficerDecisionEndpoints = {},
  ): Promise<DuplicateReviewResult> {
    const { data } = await apiClient.post<DuplicateReviewResult>(
      servicePath(endpoints.duplicate_review, `/complaints/${complaintId}/duplicates/review`),
      payload,
    )
    return data
  },

  /** Approve or reject a routing recommendation. */
  async decideRouting(
    complaintId: string,
    payload: RoutingDecisionRequest,
    endpoints: OfficerDecisionEndpoints = {},
  ): Promise<RoutingDecisionResult> {
    const { data } = await apiClient.post<RoutingDecisionResult>(
      servicePath(endpoints.routing_decision, `/complaints/${complaintId}/routing/decision`),
      payload,
    )
    return data
  },
}
