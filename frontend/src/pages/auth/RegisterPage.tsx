/**
 * Citizen self-registration.
 *
 * The password rule (at least eight characters) mirrors the server so a citizen
 * learns about a too-short password before a round trip; the server remains the
 * authority. A successful registration is already signed in, so the citizen
 * lands on the citizen portal.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api'
import { BrandLockup } from '@/components/layout/Logo'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FieldMessage, Input, Label, Select } from '@/components/ui/field'
import { useAuth } from '@/auth/AuthContext'
import { useDocumentTitle } from '@/lib/hooks'
import { LANGUAGE_LABELS } from '@/lib/labels'
import { COMPLAINT_LANGUAGES } from '@/types'
import type { ComplaintLanguage } from '@/types'

interface FormState {
  full_name: string
  email: string
  password: string
  confirm: string
  preferred_language: ComplaintLanguage
}

const EMPTY_FORM: FormState = {
  full_name: '',
  email: '',
  password: '',
  confirm: '',
  preferred_language: 'en',
}

export default function RegisterPage() {
  useDocumentTitle('Create an account')
  const { register } = useAuth()
  const navigate = useNavigate()

  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [pending, setPending] = useState(false)

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null)

    const nextErrors: Record<string, string> = {}
    if (!form.full_name.trim()) nextErrors.full_name = 'Enter your full name.'
    if (!form.email.trim()) nextErrors.email = 'Enter your email address.'
    if (!form.password) nextErrors.password = 'Choose a password.'
    else if (form.password.length < 8) nextErrors.password = 'Use at least 8 characters.'
    if (!form.confirm) nextErrors.confirm = 'Re-enter your password.'
    else if (form.confirm !== form.password) nextErrors.confirm = 'The passwords do not match.'

    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) return

    setPending(true)
    try {
      await register({
        email: form.email,
        password: form.password,
        full_name: form.full_name,
        preferred_language: form.preferred_language,
      })
      navigate('/app', { replace: true })
    } catch (failure) {
      setError(failure instanceof Error ? failure : new Error('Registration failed.'))
      if (failure instanceof ApiError) setErrors(failure.fieldErrors)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-[var(--background)] px-4 py-10">
      <div className="w-full max-w-md space-y-6">
        <div className="flex justify-center">
          <BrandLockup subtitle="Citizen portal" />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Create an account</CardTitle>
            <CardDescription>
              This creates a citizen account. Council officers are provisioned separately.
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
                <Label htmlFor="full_name">Full name</Label>
                <Input
                  id="full_name"
                  autoComplete="name"
                  value={form.full_name}
                  invalid={Boolean(errors.full_name)}
                  onChange={(event) => update('full_name', event.target.value)}
                  required
                />
                {errors.full_name ? <FieldMessage tone="error">{errors.full_name}</FieldMessage> : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  value={form.email}
                  invalid={Boolean(errors.email)}
                  onChange={(event) => update('email', event.target.value)}
                  required
                />
                {errors.email ? <FieldMessage tone="error">{errors.email}</FieldMessage> : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="new-password"
                  value={form.password}
                  invalid={Boolean(errors.password)}
                  onChange={(event) => update('password', event.target.value)}
                  required
                />
                {errors.password ? (
                  <FieldMessage tone="error">{errors.password}</FieldMessage>
                ) : (
                  <FieldMessage>At least 8 characters.</FieldMessage>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="confirm">Confirm password</Label>
                <Input
                  id="confirm"
                  type="password"
                  autoComplete="new-password"
                  value={form.confirm}
                  invalid={Boolean(errors.confirm)}
                  onChange={(event) => update('confirm', event.target.value)}
                  required
                />
                {errors.confirm ? <FieldMessage tone="error">{errors.confirm}</FieldMessage> : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="preferred_language">Preferred language</Label>
                <Select
                  id="preferred_language"
                  value={form.preferred_language}
                  invalid={Boolean(errors.preferred_language)}
                  onChange={(event) =>
                    update('preferred_language', event.target.value as ComplaintLanguage)
                  }
                >
                  {COMPLAINT_LANGUAGES.map((language) => (
                    <option key={language} value={language}>
                      {LANGUAGE_LABELS[language]}
                    </option>
                  ))}
                </Select>
                {errors.preferred_language ? (
                  <FieldMessage tone="error">{errors.preferred_language}</FieldMessage>
                ) : (
                  <FieldMessage>Used for the language the portal speaks to you in.</FieldMessage>
                )}
              </div>

              <Button type="submit" className="w-full" disabled={pending}>
                {pending ? 'Creating account…' : 'Create account'}
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="text-center text-sm text-[var(--muted-foreground)]">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-[var(--primary)] underline-offset-4 hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  )
}
