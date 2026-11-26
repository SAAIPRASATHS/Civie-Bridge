/** Citizen portal service, wrapping `/api/citizen/*`. */

import { apiClient } from './client'
import type {
  CitizenComplaintDetail,
  CitizenComplaintPage,
  ComplaintSubmissionRequest,
  ComplaintSubmissionResponse,
} from '@/types'

export const citizenApi = {
  async submit(payload: ComplaintSubmissionRequest): Promise<ComplaintSubmissionResponse> {
    const { data } = await apiClient.post<ComplaintSubmissionResponse>('/citizen/complaints', payload)
    return data
  },

  async listMine(limit = 20, offset = 0): Promise<CitizenComplaintPage> {
    const { data } = await apiClient.get<CitizenComplaintPage>('/citizen/complaints', {
      params: { limit, offset },
    })
    return data
  },

  /** A reference belonging to another citizen answers 404, by design. */
  async getMine(referenceId: string): Promise<CitizenComplaintDetail> {
    const { data } = await apiClient.get<CitizenComplaintDetail>(
      `/citizen/complaints/${encodeURIComponent(referenceId)}`,
    )
    return data
  },
}
