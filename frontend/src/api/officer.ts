/** Officer dashboard service, wrapping `/api/officer/*` and the read-only agent routes. */

import { apiClient } from './client'
import type {
  DepartmentDirectoryPage,
  DuplicateMatchesPage,
  OfficerAnalytics,
  OfficerAuditTrail,
  OfficerComplaintDetail,
  OfficerComplaintFilters,
  OfficerComplaintPage,
} from '@/types'

/**
 * Build the query string by hand.
 *
 * FastAPI reads a repeated parameter (`?status=a&status=b`) into a list, but
 * Axios' default array serialiser emits `status[]=a`, which FastAPI does not
 * recognise. A `URLSearchParams` is serialised verbatim, so the filters reach
 * the backend exactly as it documents them.
 */
function buildQuery(filters: OfficerComplaintFilters): URLSearchParams {
  const params = new URLSearchParams()
  for (const status of filters.status ?? []) params.append('status', status)
  if (filters.priority) params.set('priority', filters.priority)
  if (filters.category) params.set('category', filters.category)
  if (filters.department_id) params.set('department_id', filters.department_id)
  if (filters.ward) params.set('ward', filters.ward)
  if (filters.language) params.set('language', filters.language)
  if (filters.pending_review_only) params.set('pending_review_only', 'true')
  if (filters.search) params.set('search', filters.search)
  if (filters.limit !== undefined) params.set('limit', String(filters.limit))
  if (filters.offset !== undefined) params.set('offset', String(filters.offset))
  return params
}

export const officerApi = {
  async listComplaints(filters: OfficerComplaintFilters = {}): Promise<OfficerComplaintPage> {
    const { data } = await apiClient.get<OfficerComplaintPage>('/officer/complaints', {
      params: buildQuery(filters),
    })
    return data
  },

  async reviewQueue(limit = 20, offset = 0): Promise<OfficerComplaintPage> {
    const { data } = await apiClient.get<OfficerComplaintPage>('/officer/review-queue', {
      params: { limit, offset },
    })
    return data
  },

  async departments(): Promise<DepartmentDirectoryPage> {
    const { data } = await apiClient.get<DepartmentDirectoryPage>('/officer/departments')
    return data
  },

  async analytics(windowDays = 30): Promise<OfficerAnalytics> {
    const { data } = await apiClient.get<OfficerAnalytics>('/officer/analytics', {
      params: { window_days: windowDays },
    })
    return data
  },

  async complaintDetail(complaintId: string): Promise<OfficerComplaintDetail> {
    const { data } = await apiClient.get<OfficerComplaintDetail>(
      `/officer/complaints/${complaintId}`,
    )
    return data
  },

  async auditTrail(complaintId: string): Promise<OfficerAuditTrail> {
    const { data } = await apiClient.get<OfficerAuditTrail>(
      `/officer/complaints/${complaintId}/audit`,
    )
    return data
  },

  /** Ranked potential duplicate matches for a stored complaint. Read-only. */
  async duplicateMatches(complaintId: string, limit = 10, offset = 0): Promise<DuplicateMatchesPage> {
    const { data } = await apiClient.get<DuplicateMatchesPage>(
      `/complaints/${complaintId}/duplicates`,
      { params: { limit, offset } },
    )
    return data
  },
}
