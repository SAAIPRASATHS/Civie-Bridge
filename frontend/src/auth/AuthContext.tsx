/**
 * Authentication state.
 *
 * The server owns the session: it lives in an HttpOnly cookie that JavaScript
 * cannot read, so this provider holds no token. It keeps only the resolved
 * profile, and re-resolves that profile from `/api/auth/me` on a page load so a
 * refreshed tab restores its state without any client-side persistence.
 *
 * Nothing here is written to `localStorage` or `sessionStorage`.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { authApi, setUnauthorizedHandler } from '@/api'
import type { LoginRequest, RegisterRequest, UserProfile } from '@/types'

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous'

export interface AuthContextValue {
  user: UserProfile | null
  status: AuthStatus
  isAuthenticated: boolean
  login: (payload: LoginRequest) => Promise<UserProfile>
  register: (payload: RegisterRequest) => Promise<UserProfile>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')

  // A 401 from any request means the cookie is gone or expired; drop the cached
  // profile so the guards send the visitor back to sign in.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null)
      setStatus('anonymous')
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  // Restore the session on a cold load, if one exists.
  useEffect(() => {
    let cancelled = false
    authApi
      .me()
      .then((profile) => {
        if (cancelled) return
        setUser(profile)
        setStatus('authenticated')
      })
      .catch(() => {
        if (cancelled) return
        setUser(null)
        setStatus('anonymous')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (payload: LoginRequest) => {
    const response = await authApi.login(payload)
    setUser(response.user)
    setStatus('authenticated')
    return response.user
  }, [])

  const register = useCallback(async (payload: RegisterRequest) => {
    const response = await authApi.register(payload)
    setUser(response.user)
    setStatus('authenticated')
    return response.user
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      setUser(null)
      setStatus('anonymous')
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      status,
      isAuthenticated: status === 'authenticated' && user !== null,
      login,
      register,
      logout,
    }),
    [user, status, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
