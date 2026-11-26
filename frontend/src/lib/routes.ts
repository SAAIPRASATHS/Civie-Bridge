/** Role-aware home routes and small navigation helpers. */

import type { UserRole } from '@/types'

/** Where a signed-in account lands after authentication. */
export function homePathForRole(role: UserRole): string {
  // Administrators carry full officer access, matching `OFFICER_ROLES` on the
  // backend, and there is no separate administrator surface in this phase.
  return role === 'citizen' ? '/app' : '/officer'
}

/** The roles a given surface accepts. */
export const CITIZEN_ROLES: UserRole[] = ['citizen']
export const OFFICER_ROLES: UserRole[] = ['officer', 'admin']
