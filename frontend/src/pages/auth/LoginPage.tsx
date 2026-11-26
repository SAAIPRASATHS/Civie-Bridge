/**
 * Email and password sign-in.
 *
 * The server owns the session: it sets an HttpOnly cookie, so this page holds
 * the credentials only for the duration of the request and never persists them.
 * The demo-account list comes from a development-only endpoint, so a rejection
 * is treated as "no list to show", not as an error.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ApiError, authApi } from '@/api'
import { RoleBadge } from '@/components/common/badges'
import { BrandLockup } from '@/components/layout/Logo'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FieldMessage, Input, Label } from '@/components/ui/field'
import { useAuth } from '@/auth/AuthContext'
import { useAsyncData, useDocumentTitle } from '@/lib/hooks'
import { homePathForRole } from '@/lib/routes'
import type { DemoAccount } from '@/types'

export default function LoginPage() {
  useDocumentTitle('Sign in')
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<ApiError | Error | null>(null)

  // A disabled endpoint rejects (404); that simply means there is nothing to
  // show, so the failure is folded into an empty list.
  const demos = useAsyncData<DemoAccount[]>(
    () => authApi.demoAccounts().catch(() => [] as DemoAccount[]),
    [],
  )

  const fieldErrors = error instanceof ApiError ? error.fieldErrors : {}
  const demoAccounts = demos.data ?? []

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setPending(true)
    setError(null)
    try {
      const profile = await login({ email, password })
      const home = homePathForRole(profile.role)
      const requested = (location.state as { from?: unknown } | null)?.from
      // Honour a guard's redirect only when the destination belongs to the
      // surface this role can open; otherwise land on the role's own home.
      const target =
        typeof requested === 'string' && requested.startsWith('/') && requested.startsWith(home)
          ? requested
          : home
      navigate(target, { replace: true })
    } catch (failure) {
      setError(failure instanceof Error ? failure : new Error('Sign-in failed.'))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-[var(--background)] px-4 py-10">
      <div className="w-full max-w-md space-y-6">
        <div className="flex justify-center">
          <BrandLockup subtitle="Citizen & officer portal" />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Sign in</CardTitle>
            <CardDescription>
              Use the account you registered with the citizen portal, or your officer credentials.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleSubmit} noValidate>
              {error ? (
                <Alert tone="danger" role="alert">
                  {error instanceof ApiError ? error.userMessage : error.message}
                </Alert>
              ) : null}

              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  invalid={Boolean(fieldErrors.email)}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
                {fieldErrors.email ? <FieldMessage tone="error">{fieldErrors.email}</FieldMessage> : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  invalid={Boolean(fieldErrors.password)}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
                {fieldErrors.password ? (
                  <FieldMessage tone="error">{fieldErrors.password}</FieldMessage>
                ) : null}
              </div>

              <Button type="submit" className="w-full" disabled={pending}>
                {pending ? 'Signing in…' : 'Sign in'}
              </Button>
            </form>
          </CardContent>
        </Card>

        {demoAccounts.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Prototype accounts</CardTitle>
              <CardDescription>
                Quick-access accounts for this prototype environment only.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {demoAccounts.map((account) => (
                <div
                  key={account.email}
                  className="rounded-lg border border-[var(--border)] p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium" title={account.full_name}>
                        {account.full_name}
                      </p>
                      <p className="truncate text-xs text-[var(--muted-foreground)]" title={account.email}>
                        {account.email}
                      </p>
                    </div>
                    <RoleBadge role={account.role} />
                  </div>
                  {account.note ? (
                    <p className="mt-2 text-xs text-[var(--muted-foreground)]">{account.note}</p>
                  ) : null}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-3 w-full"
                    onClick={() => {
                      setEmail(account.email)
                      setPassword(account.password)
                    }}
                  >
                    Use this account
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}

        <p className="text-center text-sm text-[var(--muted-foreground)]">
          New to CivicBridge AI?{' '}
          <Link to="/register" className="font-medium text-[var(--primary)] underline-offset-4 hover:underline">
            Create a citizen account
          </Link>
        </p>
      </div>
    </div>
  )
}
