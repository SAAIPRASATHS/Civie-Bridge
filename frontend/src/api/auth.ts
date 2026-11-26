/** Authentication service, wrapping `/api/auth/*`. */

import { apiClient } from './client'
import type {
  DemoAccount,
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  UserProfile,
} from '@/types'

export const authApi = {
  async login(payload: LoginRequest): Promise<LoginResponse> {
    const { data } = await apiClient.post<LoginResponse>('/auth/login', payload)
    return data
  },

  async register(payload: RegisterRequest): Promise<LoginResponse> {
    const { data } = await apiClient.post<LoginResponse>('/auth/register', payload)
    return data
  },

  async logout(): Promise<void> {
    await apiClient.post('/auth/logout')
  },

  /** Resolve the session cookie to a profile; rejects with 401 when there is none. */
  async me(): Promise<UserProfile> {
    const { data } = await apiClient.get<UserProfile>('/auth/me')
    return data
  },

  /**
   * The synthetic demo accounts. Only available in a development/test backend;
   * a 404 means the endpoint is disabled and the sign-in form simply shows no
   * quick-access list.
   */
  async demoAccounts(): Promise<DemoAccount[]> {
    const { data } = await apiClient.get<DemoAccount[]>('/auth/demo-accounts')
    return data
  },
}
