/** Authentication payloads, mirroring `app/api/auth.py`. */

import type { ComplaintLanguage, UserRole } from './common'

export interface UserProfile {
  id: string
  email: string
  full_name: string
  role: UserRole
  preferred_language: ComplaintLanguage
  is_active: boolean
}

export interface LoginResponse {
  user: UserProfile
  expires_in_seconds: number
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  email: string
  password: string
  full_name: string
  preferred_language: ComplaintLanguage
}

/**
 * A synthetic prototype account. `GET /api/auth/demo-accounts` is disabled
 * outside development and test, so the sign-in page treats a 404 as "no demo
 * accounts available" rather than an error.
 */
export interface DemoAccount {
  role: UserRole
  email: string
  password: string
  full_name: string
  note: string
}
