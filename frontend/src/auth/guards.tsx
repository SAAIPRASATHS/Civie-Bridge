import { Navigate, Outlet, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'

import { LoadingState } from '@/components/common/states'
import { useAuth } from '@/auth/AuthContext'
import { homePathForRole } from '@/lib/routes'
import type { UserRole } from '@/types'

/**
 * Route guards.
 *
 * These are a usability layer, not the security boundary: the backend re-checks
 * every request's session and role, so a citizen who reached an officer URL
 * would still be refused by the API. The guard exists so the app never renders
 * a screen whose data can only fail to load.
 */

function FullPageSpinner() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-[var(--background)]">
      <LoadingState label="Restoring your session" />
    </div>
  )
}

/** Requires any signed-in account. */
export function RequireAuth() {
  const { status, user } = useAuth()
  const location = useLocation()

  if (status === 'loading') return <FullPageSpinner />
  if (status === 'anonymous' || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <Outlet />
}

/** Requires a signed-in account holding one of `roles`. */
export function RequireRole({ roles }: { roles: UserRole[] }) {
  const { status, user } = useAuth()
  const location = useLocation()

  if (status === 'loading') return <FullPageSpinner />
  if (status === 'anonymous' || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (!roles.includes(user.role)) {
    // Send the visitor to their own home rather than a dead end. A citizen
    // hitting an officer URL lands back on the citizen dashboard.
    return <Navigate to={homePathForRole(user.role)} replace />
  }
  return <Outlet />
}

/** Sends an already-signed-in visitor away from the sign-in and register pages. */
export function RedirectIfAuthenticated({ children }: { children: ReactNode }) {
  const { status, user } = useAuth()
  if (status === 'loading') return <FullPageSpinner />
  if (status === 'authenticated' && user) {
    return <Navigate to={homePathForRole(user.role)} replace />
  }
  return <>{children}</>
}
